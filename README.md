# Roco 模拟器

## 运行方式

**环境**：conda 虚拟环境 `roco`（Python 3.14）。

```bash
conda activate roco
```

项目本身只依赖标准库，要求 Python ≥ 3.10（代码使用 `dataclass(kw_only=True)`）。

在项目根目录执行，开三个终端，**每个终端都要先 `conda activate roco`**：

```bash
# 终端 1：服务端（等待两个客户端接入，跑完一局后退出）
python server.py

# 终端 2：A 方客户端
python client.py --team team0

# 终端 3：B 方客户端
python client.py --auto --team team1
```

### `--team` 必须显式指定

现有队伍见 `data/teams/`（`team0.json` ~ `team13.json`）。两种写法都可用：

```bash
--team data/teams/team0.json 
--team team0             
```

### 参数

| 程序        | 参数       | 默认值                    | 说明                             |
| ----------- | ---------- | ------------------------- | -------------------------------- |
| `server.py` | `--host`   | `127.0.0.1`               | 监听地址                         |
| `server.py` | `--port`   | `5000`                    | 监听端口                         |
| `client.py` | `--host`   | `127.0.0.1`               | 服务端地址                       |
| `client.py` | `--port`   | `5000`                    | 服务端端口                       |
| `client.py` | `--team`   | `data/teams/冻陨2.0.json` | **必须显式指定**，缺省文件不存在 |
| `client.py` | `--auto`   | 关闭                      | 开启后全自动对战                 |
| `client.py` | `--nature` | `-1`                      | 性格编号，`-1` 为无性格          |

双方 `--host` / `--port` 需与服务端一致。

### 日志

每次启动**覆盖写入** `logs/battle/`：

| 文件          | 内容                 |
| ------------- | -------------------- |
| `server.log`  | 服务端完整回合日志   |
| `clientA.log` | A 方视角的状态与操作 |
| `clientB.log` | B 方视角的状态与操作 |

日志目录不存在时会自动创建。
