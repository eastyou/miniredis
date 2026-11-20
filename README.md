# miniredis

[![CI](https://github.com/eastyou/miniredis/actions/workflows/ci.yml/badge.svg)](https://github.com/eastyou/miniredis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-brightgreen.svg)](pyproject.toml)

用 Python 标准库从零写一个 Redis：asyncio TCP 服务端、RESP2 线协议、四种数据结构、键过期、AOF 持久化。**零第三方依赖**，`pip install -e .` 之后就能用 `redis-cli` 连上来。

这个项目的目的不是替代 Redis，而是把「一个真实数据库服务端到底由哪些部分构成」拆开讲清楚：协议怎么分帧、过期怎么实现、并发连接怎么管理、持久化怎么重放。

## 特性

- **完整 RESP2 协议**：数组请求、行内命令、流水线（pipelining）、分片到达的增量解析
- **四种数据结构**：string、list、hash、set，以及键级别的 TTL
- **过期时间**：`EXPIRE` / `TTL` / `PERSIST`，惰性删除，时钟可注入便于测试
- **AOF 持久化**：写命令追加落盘，重启自动重放，容忍文件末尾被截断
- **优雅关闭**：响应 `SIGINT` / `SIGTERM`，停止监听并关闭 AOF
- **可观测的小细节**：错误消息与 Redis 对齐（`WRONGTYPE`、`ERR wrong number of arguments`），`QUIT` 正常断连
- **79 个单元测试**：协议编解码、命令语义、过期边界、真实 socket 集成、AOF 重放

## 支持的命令

| 分类 | 命令 |
| --- | --- |
| 连接 | `PING` `ECHO` `QUIT` |
| 字符串 | `SET`（支持 `EX` / `PX`）`GET` `INCR` `DECR` |
| 键 | `DEL` `EXISTS` `EXPIRE` `TTL` `PERSIST` `KEYS` `TYPE` `DBSIZE` `FLUSHDB` |
| 列表 | `LPUSH` `RPUSH` `LRANGE` `LLEN` |
| 哈希 | `HSET` `HGET` `HDEL` `HLEN` |
| 集合 | `SADD` `SREM` `SMEMBERS` `SCARD` |

## 安装

```bash
pip install "git+https://github.com/eastyou/miniredis.git"
```

或本地开发：

```bash
git clone https://github.com/eastyou/miniredis.git
cd miniredis
python3 -m pip install -e .
```

不安装也能直接跑：`python3 -m miniredis --port 6399`

## 快速开始

启动服务端：

```console
$ miniredis-server --port 6399 --aof data.aof
miniredis 已启动: redis://127.0.0.1:6399（Ctrl+C 退出）
```

用自带客户端：

```console
$ miniredis-cli SET greeting "hello"
OK
$ miniredis-cli GET greeting
hello
$ miniredis-cli RPUSH queue a b c
(integer) 3
$ miniredis-cli LRANGE queue 0 -1
1) a
2) b
3) c
```

也可以用官方 `redis-cli`，协议是兼容的：

```console
$ redis-cli -p 6399
127.0.0.1:6399> SET token abc EX 60
OK
127.0.0.1:6399> TTL token
(integer) 60
```

## 服务端参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--host` | 监听地址 | `127.0.0.1` |
| `--port` | 监听端口，`0` 表示由系统分配 | `6399` |
| `--aof PATH` | 开启 AOF 持久化并指定文件 | 关闭 |

## 架构

```
miniredis/
├── protocol.py   RESP2 编解码 + 增量解析器 + 客户端回复读取
├── store.py      数据结构、惰性过期、可注入时钟
├── commands.py   命令分派、参数校验、错误语义
├── aof.py        命令日志追加与重放
├── server.py     asyncio 服务端、连接生命周期、优雅关闭
└── cli.py        命令行客户端
```

几个实现上的取舍：

- **请求解析是增量的**。TCP 不保证一条命令一次到达，`CommandParser` 内部维护字节缓冲区，凑齐一条才消费，绝不阻塞在「读一行」上。
- **过期是惰性的**。读键时判断 `expire_at`，过期就顺手删除；不引入后台清理线程，避免并发与锁的问题。
- **AOF 只记录写命令**。`GET`、`PING` 这类不会改变状态的命令不落盘，重放时逐条执行并忽略错误。
- **回复直接编码成 bytes**。命令处理函数返回已编码的 RESP 字节，服务端把同一批请求的回复拼成一次 `write`，减少系统调用。

## 已知边界

这是一个教学向实现，刻意没有做：多数据库（`SELECT`）、主从复制、事务（`MULTI/EXEC`）、过期键主动清理、AOF 重写、`SET NX/XX`、集群。AOF 重放 `SET ... EX` 时 TTL 会从重启时刻重新计算。

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
```

测试覆盖协议分片解析、命令语义、TTL 边界、真实 socket 通信与 AOF 重放，CI 覆盖 Python 3.9 ~ 3.13。

## License

[MIT](LICENSE) © 2026 eastyou
