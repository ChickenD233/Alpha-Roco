# Alpha Roco 模拟器

## 运行方式

**环境**：conda 虚拟环境 `roco`（Python 3.14）。

```bash
conda activate roco
```

项目本身只依赖标准库，要求 Python ≥ 3.10（代码使用 `dataclass(kw_only=True)`）。

在项目根目录执行，开三个终端，每个终端先 `conda activate roco`：

```bash
python server.py
python client.py --team team0
python client.py --auto --team team1
```

队伍配置见 `data/teams/team0.json` 至 `team13.json`，`--team` 可传队伍名称或文件路径。

## 训练

单个recurrent PPO网络控制A、B双方；每局双方从team0至team13随机选队。每批28局、2个PPO epoch，更新共享策略／价值与对手交叉熵分支。默认输出到`logs/az/`：

- `checkpoint-batch-*.pt`：候选checkpoint，可恢复。
- `best.pt`：冻结最佳模型。
- `metrics.jsonl`：训练批胜负、policy/value/entropy/KL/梯度、对手交叉熵、逐批对best探测胜率。
- `eval.jsonl`：每侧胜率、区间、Elo与晋升结果。
- `probe_table.md`：逐批对best探测胜率表，每批自动更新。

设置`--probe-games`后，每批对best跑该局数并记录两侧胜率，探测不触发晋升；每`--eval-interval`批做一次完整评测（默认200局），两侧胜率都超过0.55才晋升。`--eval-workers`、`--probe-workers`控制并行数。`--log-games`记录逐局，`--keep-checkpoints`控制候选checkpoint数量。

逐批探测结果自动写入`probe_table.md`；也可重新生成：

```bash
python -m alpha_roco.report --input logs/az/metrics.jsonl --output logs/az/probe_table.md
```

CPU短smoke：

```bash
python -m alpha_roco.train --batches 1 --games-per-batch 1 --ppo-epochs 1 --device cpu --output logs/az-smoke-cpu --eval-interval 0
```

CUDA短smoke：

```bash
python -m alpha_roco.train --batches 1 --games-per-batch 1 --ppo-epochs 1 --device cuda --output logs/az-smoke-cuda --eval-interval 0
```

恢复时载入checkpoint中的模型与训练配置；`--batches`为额外批数，`--ppo-epochs`、设备、线程、输出目录、逐局日志、候选checkpoint数量和评测参数可覆盖：

```bash
python -m alpha_roco.train --batches 10 --resume logs/az/checkpoint-batch-000010.pt --output logs/az
```

## 模型对战

A、B两侧共用同一checkpoint，各自维护记忆：

```bash
python -m alpha_roco.play --checkpoint logs/az/best.pt --team-a team4 --team-b team0 --sample --seed 22
```

`--sample`按策略采样；`--random-init`运行随机初始化模型。

## 固定种子评测

比较两个单网络checkpoint；每局双方随机选队，candidate在A、B两侧各半。评测默认4路并行，`--workers`控制并行数。只输出两侧胜率，不输出合计：

```bash
python -m alpha_roco.evaluate --candidate logs/az/checkpoint-batch-000020.pt --reference logs/az/best.pt --games 400 --sample
```

`--sample`按策略采样，默认取最大概率动作。
