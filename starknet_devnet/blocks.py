"""
Class for generating and handling blocks
"""

from typing import Any, Dict, List, Optional, Sequence, Union

from starkware.starknet.core.os.block_hash.block_hash import (
    calculate_block_hash,
    calculate_event_hash,
)
from starkware.starknet.definitions.error_codes import StarknetErrorCode
from starkware.starknet.services.api.feeder_gateway.response_objects import (
    LATEST_BLOCK_ID,
    PENDING_BLOCK_ID,
    BlockIdentifier,
    BlockStateUpdate,
    BlockStatus,
    StarknetBlock,
)
from starkware.starknet.testing.state import StarknetState
from starkware.starkware_utils.error_handling import StarkErrorCode

from .constants import CAIRO_LANG_VERSION, DUMMY_STATE_ROOT
from .origin import Origin
from .state_archive import MemoryStateArchive
from .transactions import DevnetTransaction
from .util import StarknetDevnetException


def _parse_block_hash(raw: Optional[str]):
    if raw is None:
        return raw

    try:
        if raw.startswith("0x"):
            try:
                return int(raw, 16)
            except ValueError:
                pass

        raise StarknetDevnetException(
            code=StarkErrorCode.MALFORMED_REQUEST,
            message=f"Block hash should be a hexadecimal string starting with 0x, or 'null'; got: '{raw}'.",
        )
    except ValueError as error:
        raise StarknetDevnetException(
            code=StarkErrorCode.MALFORMED_REQUEST,
            message=f"Invalid block hash: '{raw}'",
        ) from error


def _parse_block_number(raw: Optional[Union[int, str]]) -> BlockIdentifier:
    if raw is None:  # no ID provided
        return LATEST_BLOCK_ID

    if raw in [PENDING_BLOCK_ID, LATEST_BLOCK_ID]:
        return raw

    if isinstance(raw, int):  # already a parsed number
        return raw

    if raw.isdigit():  # string that contains a numeric ID
        try:
            return int(raw, 10)
        except ValueError:
            pass

    raise StarknetDevnetException(
        code=StarkErrorCode.MALFORMED_REQUEST, message=f"Invalid block number: '{raw}'"
    )


# pylint: disable=too-many-instance-attributes
class DevnetBlocks:
    """This class is used to store the generated blocks of the devnet."""

    def __init__(self, origin: Origin, lite=False) -> None:
        self.origin = origin
        self.lite = lite
        self.__hash2block: Dict[int, StarknetBlock] = {}
        self.__state_updates: Dict[int, BlockStateUpdate] = {}
        self.__num2hash: Dict[int, int] = {}
        self.__pending_block: StarknetBlock = None
        self.__pending_state_update: BlockStateUpdate = None
        self.__pending_signatures: Sequence[List[int]] = None
        self.__state_archive = MemoryStateArchive()

    async def get_last_block(self) -> StarknetBlock:
        """Returns the last block stored so far."""
        return await self.get_by_number(self.get_number_of_accepted_blocks() - 1)

    def get_number_of_accepted_blocks(self) -> int:
        """Returns the number of not aborted blocks."""
        return len(self.__num2hash) + self.origin.get_number_of_blocks()

    def get_next_block_number(self) -> int:
        """Returns the block_number of the next block"""
        return self.get_number_of_accepted_blocks()

    def __assert_block_number_in_range(self, block_number: BlockIdentifier):
        if block_number < 0:
            message = (
                f"Block number must be a non-negative integer; got: {block_number}."
            )
            raise StarknetDevnetException(
                code=StarkErrorCode.MALFORMED_REQUEST, message=message
            )
        number_of_accepted_blocks = self.get_number_of_accepted_blocks()
        if block_number >= number_of_accepted_blocks:
            message = f"Block number too high. There are currently {number_of_accepted_blocks} blocks; got: {block_number}."
            raise StarknetDevnetException(
                code=StarknetErrorCode.BLOCK_NOT_FOUND, message=message
            )

    async def get_by_number(self, block_number: Optional[str]) -> StarknetBlock:
        """Returns the block whose block_number is provided"""
        block_number = _parse_block_number(block_number)

        if block_number == PENDING_BLOCK_ID:
            if self.__pending_block:
                return self.__pending_block
            # if no pending, default to latest
            block_number = LATEST_BLOCK_ID

        if block_number == LATEST_BLOCK_ID:
            if self.__num2hash:
                return await self.get_last_block()
            return await self.origin.get_block_by_number(block_number)

        self.__assert_block_number_in_range(block_number)
        if block_number in self.__num2hash:
            return self.__hash2block[self.__num2hash[block_number]]

        return await self.origin.get_block_by_number(block_number)

    async def get_by_hash(self, block_hash: str) -> StarknetBlock:
        """
        Returns the block with the given block hash.
        """
        numeric_hash = _parse_block_hash(block_hash)

        if numeric_hash in self.__hash2block:
            return self.__hash2block[numeric_hash]

        return await self.origin.get_block_by_hash(block_hash)

    async def get_state_update(
        self, block_hash: str = None, block_number: Any = None
    ) -> BlockStateUpdate:
        """
        Returns state update for the provided block hash or block number.
        It will return the last state update if block is not provided.
        """
        if block_hash:
            numeric_hash = _parse_block_hash(block_hash)

            if numeric_hash not in self.__hash2block:
                return await self.origin.get_state_update(block_hash=block_hash)

            block_number = self.__hash2block[numeric_hash].block_number

        block_number = _parse_block_number(block_number)

        if block_number == PENDING_BLOCK_ID:
            if self.__pending_state_update:
                return self.__pending_state_update
            # if no pending, default to latest
            block_number = LATEST_BLOCK_ID

        # now either an int or "latest"
        if block_number != LATEST_BLOCK_ID:
            self.__assert_block_number_in_range(block_number)
            numeric_hash = self.__num2hash[block_number]
            if numeric_hash in self.__state_updates:
                return self.__state_updates[numeric_hash]

            return await self.origin.get_state_update(block_hash=numeric_hash)

        # now it's the latest
        return (
            self.__state_updates.get((await self.get_last_block()).block_hash)
            or await self.origin.get_state_update()
        )

    async def generate_pending(
        self,
        transactions: List[DevnetTransaction],
        state: StarknetState,
        state_update=None,
    ):
        """
        Generates pending objects (block, updates) and stores them as private properties.
        The method `store_pending` can be used after this method.
        """
        timestamp = state.state.block_info.block_timestamp
        signatures = [tx.get_signature() for tx in transactions or []]
        internal_transactions = [tx.internal_tx for tx in transactions or []]
        transaction_receipts = tuple(tx.get_execution() for tx in transactions or ())

        block_number = self.get_number_of_accepted_blocks()
        if block_number == 0:
            parent_block_hash = 0
        else:
            last_block = await self.get_last_block()
            parent_block_hash = last_block.block_hash

        self.__pending_block = StarknetBlock.create(
            block_hash=None,
            block_number=None,
            state_root=None,
            transactions=internal_transactions,
            timestamp=timestamp,
            transaction_receipts=transaction_receipts,
            status=BlockStatus.PENDING,
            gas_price=state.state.block_info.gas_price,
            sequencer_address=state.general_config.sequencer_address,
            parent_block_hash=parent_block_hash,
            starknet_version=CAIRO_LANG_VERSION,
        )

        self.__pending_state_update = state_update
        self.__pending_signatures = signatures

    async def generate_empty_block(
        self, state: StarknetState, state_update: BlockStateUpdate
    ) -> StarknetBlock:
        """Generate an empty block"""
        await self.generate_pending(
            transactions=[], state=state, state_update=state_update
        )
        return await self.store_pending(state, is_empty_block=True)

    async def __calculate_pending_block_hash(
        self, state: StarknetState, block_number: int, state_root: bytes
    ):
        event_hashes: List[int] = []
        for receipt in self.__pending_block.transaction_receipts:
            for event in receipt.events:
                event_hashes.append(
                    calculate_event_hash(
                        from_address=event.from_address,
                        keys=event.keys,
                        data=event.data,
                    )
                )

        return await calculate_block_hash(
            general_config=state.general_config,
            parent_hash=self.__pending_block.parent_block_hash,
            block_number=block_number,
            global_state_root=state_root,
            block_timestamp=self.__pending_block.timestamp,
            tx_hashes=[tx.transaction_hash for tx in self.__pending_block.transactions],
            tx_signatures=self.__pending_signatures,
            event_hashes=event_hashes,
            sequencer_address=self.__pending_block.sequencer_address,
        )

    def is_block_pending(self) -> bool:
        """Return `True` if there is a pending block, oterhwise return `False`"""
        return self.__pending_block is not None

    async def store_pending(
        self, state: StarknetState, is_empty_block=False, block_hash=None
    ) -> StarknetBlock:
        """
        Store pending block, assign a block hash to it, effecitvely making it the latest.
        Set pending properties to None.
        """
        assert self.__pending_block

        block_dict = self.__pending_block.dump()

        block_dict["status"] = BlockStatus.ACCEPTED_ON_L2.name
        state_root = DUMMY_STATE_ROOT
        block_dict["state_root"] = state_root.hex()

        block_number = self.get_next_block_number()
        block_dict["block_number"] = block_number

        if self.lite or is_empty_block:
            block_hash = block_number
        elif block_hash is None:
            block_hash = await self.__calculate_pending_block_hash(
                state, block_number, state_root
            )

        block_dict["block_hash"] = hex(block_hash)
        self.__num2hash[block_number] = block_hash

        if self.__pending_state_update is not None:
            self.__pending_state_update = BlockStateUpdate(
                block_hash=block_hash,
                old_root=self.__pending_state_update.old_root,
                new_root=self.__pending_state_update.new_root,
                state_diff=self.__pending_state_update.state_diff,
            )
        self.__state_updates[block_hash] = self.__pending_state_update
        self.__pending_state_update = None

        block = StarknetBlock.load(block_dict)
        self.__hash2block[block.block_hash] = block
        self.__state_archive.store(block_hash, state)

        self.__pending_block = None
        self.__pending_signatures = None
        return block

    def get_state(self, block_hash: int) -> StarknetState:
        """Return state at block with `number`"""
        return self.__state_archive.get(block_hash)

    @staticmethod
    def get_numeric_hash(block_hash: int):
        """Get numeric hash."""
        return _parse_block_hash(block_hash)

    async def abort_latest_block(self, block_hash: str) -> str:
        """
        Abort latest block.
        """
        numeric_hash = _parse_block_hash(block_hash)
        block = self.__hash2block[numeric_hash]

        # This is done like this because the block object's properties cannot be modified
        block_dict = block.dump()
        block_dict["status"] = BlockStatus.ABORTED.name
        block_dict["transaction_receipts"] = None
        del self.__num2hash[block_dict["block_number"]]
        block_dict["block_number"] = None
        self.__hash2block[numeric_hash] = StarknetBlock.load(block_dict)
        self.__state_archive.remove(numeric_hash)

        return block.block_hash
