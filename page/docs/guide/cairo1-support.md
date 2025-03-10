---
sidebar_position: 18
---

# Cairo 1 support

Declaring, deploying and interacting with Cairo 1 contracts is supported in the latest version of starknet-devnet. Declaration body only includes the [Sierra](https://docs.starknet.io/documentation/architecture_and_concepts/Contracts/cairo-1-and-sierra/) of your contract class, so Devnet needs to compile it to Casm.

Make sure your Devnet is using the same compiler version that you used for the compilation of the contract class being declared. To successfully declare a contract compiled with v2.0.0, if on an x86 machine, you don't have to do anything. On another architecture, or if you want to specify a custom version of the Cairo 1 compiler, you need to specify your local compiler. Use one of:

- `--cairo-compiler-manifest <PATH_TO_CARGO_TOML>`
- `--sierra-compiler-path <PATH_TO_SIERRA_EXECUTABLE>`

## Compiler args

By default, Devnet runs the compiler with `--add-pythonic-hints`. Modify this with Devnet's `--compiler-args` CLI argument. You will probably want to use the same set of flags that you used when originally compiling. E.g. if in your development environment you ran `starknet-sierra-compile` with `--add-pythonic-hints` and `--allowed-libfuncs-list-file /my/path/to/lib_funcs.json`, you should start Devnet with (notice the quotes):

```
$ starknet-devnet --compiler-args "--add-pythonic-hints --allowed-libfuncs-list-file /my/path/to/lib_funcs.json"`
```

## Cairo 2 support

There is no difference in how Devnet treats Cairo 2 contracts, so the [paragraph on Cairo 1 support](#cairo-1-support) should be applicable.

## Compiler binaries

Other than cloning [the compiler repo](https://github.com/starkware-libs/cairo) and checking out and building the desired version, you can find statically linked **prebuilt** executable binaries under `Assets` of every release [on the GitHub release page](https://github.com/starkware-libs/cairo/releases) (usually x86 and Apple sillicon binaries are included).

## Docker support

Devnet's Docker image has a Cairo compiler set up internally, so Cairo 1 is supported out-of-the-box. But to use a custom compiler, you should have a statically linked executable binary sierra compiler on your host and use it like this (use absolute paths when mounting):

```
$ docker run -it \
    -p <YOUR_PORT>:5050 \
    --mount type=bind,source=<YOUR_PATH>,target=/starknet-sierra-compile \
    -it \
    shardlabs/starknet-devnet:<TAG> \
    --sierra-compiler-path /starknet-sierra-compile
```
