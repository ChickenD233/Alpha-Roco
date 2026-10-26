# Alpha Roco 模型设计

## 1. 网络结构

### 1.1 基本配置

#### 1.1.1 输入

- 可见特征、可见性掩码、有序事件及候选动作；数值归一化，未知值使用占位符。
- 派生特征：属性克制倍率、本系加成、预计伤害占比、预计击杀回合、速度先后、能量是否足够、技能静态属性。
- 对手预测分支仅接收公开信息。

#### 1.1.2 超参数

- 隐藏维度 `384`；Attention 为 `8` 头、`3` 层，FFN 隐藏维度为 `4 × 384`；两路记忆各使用 2 层 `GRU(384,384)`。
- 身份／类别 Embedding 为 `32／16` 维，阶段／顺序编码为 `384` 维。
- 训练超参数见第 3 节。

#### 1.1.3 可学习参数

- Embedding 表、Linear／Attention／GRU 的权重与偏置、LayerNorm 的缩放与偏移。
- 首发与战斗共享己方精灵编码器参数；对手分支参数独立。

#### 1.1.4 环境

- 设备：RTX 4080 SUPER（16 GB）。
- 时限：单次决策上限 `20` 秒。

### 1.2 输入信息与编码器

输入使用具体的信息名称；网络输出称为对应的编码向量，图中简写为“精灵编码：384”等，数字表示维度。

记号：`FFN(a,b,c) = Linear(a,b) → GELU → Linear(b,c)`；`F` 为输入维度；`MaskedMean` 对有效项求平均。

#### 1.2.1 精灵、技能与效果编码

- 精灵信息：身份、类别、主属性、副属性、性格、数值或区间、mask。
- 技能信息：身份、类别、属性、威力、能耗、应对类别、打断、传动、迅捷、蓄力、可用、明暗、mask。
- 效果信息：类型、数值、mask。

```mermaid
flowchart TB
    S0["技能信息：ID／类别／属性／威力／能耗／应对／打断／传动／迅捷／蓄力／可用／明暗／mask"] --> S1["Embedding ＋ 数值拼接"]
    S1 --> S2["FFN(F,384,384) ＋ LayerNorm"]
    S2 --> S3["MaskedMean：技能汇总编码 384"]
    S2 --> SK["保留每个技能的编码：384"]

    B0["效果信息：类型／数值／mask"] --> B1["Embedding ＋ 数值拼接"]
    B1 --> B2["FFN(F,384,384) ＋ LayerNorm"]
    B2 --> B3["MaskedMean：效果汇总编码 384"]

    P0["精灵信息：ID／类别／主副属性／性格／数值／mask"] --> P1["Embedding ＋ 数值拼接"]
    P1 --> P2["FFN(F,384,384) ＋ LayerNorm"]
    P2 --> C["Concat：1152"]
    S3 --> C
    B3 --> C
    C --> P3["FFN(1152,256,384) ＋ LayerNorm：精灵编码 384"]
```

- 主属性与副属性分别编码；己方性格使用独立 Embedding；对方不可见数值按个体值与性格范围推算为区间输入。

#### 1.2.2 战场状态与决策编码

| 输入 | 编码内容 |
|---|---|
| 当前决策信息 `DECISION` | 决策类型、阶段、时序位置与参与方 |
| 精灵信息 | 身份、类别、主副属性、性格、数值或区间、关联技能／效果与可见性掩码 |
| 战场状态项 | 状态类型、所属方、数值与可见性掩码；每项独立编码 |

- 各类型使用 `Embedding＋FFN(F_type,384,384)＋LayerNorm`；Attention 读取更新后的决策编码向量，并保留每个精灵的编码向量。

```mermaid
flowchart LR
    D["当前决策信息：类型／阶段／时序位置"] --> DE["Embedding ＋ FFN ＋ LayerNorm：决策编码"]
    S["战场状态项：按类型分项"] --> SE["按类型 Embedding ＋ FFN ＋ LayerNorm：战场状态编码"]
    P["各精灵编码"] --> A["3 × Attention Block ＋ LayerNorm"]
    DE --> A
    SE --> A
    A --> Q["读取决策编码：384"]
    A --> PE["保留每个精灵的编码：384"]
```

#### 1.2.3 阶段事件编码

- 事件记录：类型、阶段／实例／父阶段、关联动作、来源／目标、顺序、数值与可见性。
- 每条事件独立编码，包含阶段边界；同类事件跨阶段共享 FFN。
- 事件编码向量：`LayerNorm(FFN_type(事件字段编码)＋阶段 Embedding＋顺序编码)`，维度 `384`；按已知前缀送入 GRU。

```mermaid
flowchart TB
    P["阶段边界记录"] --> PM["Embedding ＋ FFN(F_type,384,384)"]
    A["动作事件记录"] --> AM["Embedding ＋ FFN(F_type,384,384)"]
    D["数值事件记录"] --> DM["Embedding ＋ 数值 ＋ FFN(F_type,384,384)"]
    C["状态事件记录"] --> CM["Embedding ＋ 数值 ＋ FFN(F_type,384,384)"]
    PM --> V["阶段 Embedding ＋ 顺序编码 ＋ LayerNorm"]
    AM --> V
    DM --> V
    CM --> V
    V --> T["按公开顺序输出各事件编码：384"]
    H["上一层／上一事件隐状态：2 × 384"] --> R["GRU(384,384)：2 层"]
    T --> R
    R --> N["当前已知事件前缀的记忆：2 × 384"]
```

### 1.3 Attention Block

首发、己方战斗局面、对手公开局面各使用 `3` 个下列 Block，输出接 LayerNorm；各分支分别设置 Block 参数。

```mermaid
flowchart LR
    X["输入编码向量：每项 384 维"] --> N1["LayerNorm"]
    N1 --> A["Masked Multi-Head Self-Attention：8 头"]
    A --> R1["残差相加"]
    X --> R1
    R1 --> N2["LayerNorm"]
    N2 --> F["FFN(384,1536,384)"]
    F --> R2["残差相加"]
    R1 --> R2
    R2 --> Y["更新后的编码向量：每项 384 维"]
```

### 1.4 首发模块

```mermaid
flowchart TB
    I["双方可见精灵信息"] --> E["己方精灵编码器"]
    D["当前决策信息"] --> DE["Embedding ＋ FFN ＋ LayerNorm：决策编码"]
    E --> A["3 × Attention Block ＋ LayerNorm"]
    DE --> A
    A --> P["六个己方精灵编码＋决策编码：各拼接为 768"]
    P --> S["共享 FFN(768,128,1)：六个分数"]
    S --> O["Softmax：6 维策略"]
    A --> G["读取决策编码：384"]
    G --> V["FFN(384,128,1) ＋ 2 × Tanh：首发价值"]
```

### 1.5 战斗模块

- 两路 2 层 GRU 分别编码己方可见历史和公共历史，后者形成隐式信念与行为倾向表示。
- 战斗阶段的可选动作由技能、聚能、换人组成；对方候选由同一动作集合生成。
- 技能候选包含动作类别、精灵、技能、明暗分支、威力、能耗、使用后能量、是否可支付、相对对方当前精灵的属性克制倍率、本系加成、预计伤害占对方当前 HP 比例、预计击杀回合、速度先后，以及应对类别、打断、传动、迅捷、蓄力、可用标记；聚能候选的派生特征取中性值。
- 换人候选额外包含入场精灵对对方当前精灵的进攻与防守克制关系；对方候选包含其技能相对我方当前精灵的克制倍率与预计伤害。
- 对手分支对候选动作输出概率；决策分支融合状态、记忆与信念，输出基础分数及按对手概率加权的动作交互分数。

```mermaid
flowchart TB
    subgraph SELF["己方状态与记忆"]
        S0["己方可见信息与当前请求"] --> S1["1.2 编码器：精灵／战场状态／决策编码"]
        S1 --> S2["3 × Attention Block ＋ LayerNorm"]
        S2 --> S3["读取决策编码：384"]
        H0["初始化事件／新增公开事件／己方提交记录"] --> H1["阶段事件编码器：1.2.3，各事件编码 384"]
        HP["己方隐状态 h_self：2 × 384"] --> HR["GRU(384,384)：2 层"]
        H1 --> HR
        HR --> HN["更新后 h_self：2 × 384"]
        HN -.->|"下一事件，复用参数"| HP
    end

    subgraph OPP["对手预测分支"]
        O0["双方公开信息与可见阶段"] --> O1["对手参数的 1.2 编码器：精灵／战场状态／决策编码"]
        O1 --> O2["3 × Attention Block ＋ LayerNorm"]
        O2 --> O3["读取公开决策编码：384"]
        E0["公开初始化事件／新增公开事件"] --> E1["对手参数的阶段事件编码器：1.2.3"]
        EP["公开隐状态 h_public：2 × 384"] --> ER["GRU(384,384)：2 层"]
        E1 --> ER
        ER --> EN["h_public：2 × 384；隐式信念与行为倾向"]
        EN -.->|"下一事件，复用参数"| EP
        OC["对方候选身份、公开特征与假设标记"] --> OR["对方候选派生特征：克制倍率／预计伤害"]
        OR --> OE["Embedding ＋ FFN(F,384,384)"]
        OE --> OP["Concat：公开局面＋记忆＋候选，1152"]
        O3 --> OP
        EN --> OP
        OP --> OS["共享 FFN(1152,128,1) ＋ Softmax"]
        OS --> Q["对手行动分布 q"]
    end

    Q --> DQ["stop-gradient：q"]
    EN --> DH["stop-gradient：h_public"]
    S3 --> F0["Concat：局面＋己方记忆＋公开信念，1152"]
    HN --> F0
    DH --> F0
    F0 --> F1["FFN(1152,256,384) ＋ LayerNorm：决策上下文"]

    AC["己方完整合法候选"] --> AR["候选派生特征：克制倍率／本系／预计伤害／击杀回合／速度／能量／技能静态属性"]
    AR --> AE["关联精灵／技能编码＋动作类别 Embedding；FFN(F,384,384)"]
    S2 --> AE
    S1 --> AE
    OR --> PE["共享参数的动作 Embedding ＋ FFN(F,384,384)"]

    F1 --> PA["Concat：上下文＋己方动作＋对方动作，1152"]
    AE --> PA
    PE --> PA
    PA --> PS["共享 FFN(1152,128,1)：逐对交互分数"]
    PS --> SUM["按 q 加权求和：每个己方动作一个分数"]
    DQ --> SUM
    F1 --> BA["Concat：上下文＋己方动作，768"]
    AE --> BA
    BA --> BS["共享 FFN(768,128,1)：基础分数"]
    SUM --> ADD["基础分数＋交互分数"]
    BS --> ADD
    ADD --> PI["合法候选 Softmax：战斗策略"]
    F1 --> V["FFN(384,128,1) ＋ 2 × Tanh：战斗价值"]
```

### 1.6 时间连接

- 两路 2 层 GRU 独立参数、时间共享；隐状态按 episode 清零，按新增事件更新并去重，在决策点读取。
- $h_k=z_k\odot h_{k-1}+(1-z_k)\odot n_k$，$z_k$ 为保留门，$n_k$ 为候选隐状态。

```mermaid
flowchart LR
    H["历史隐状态：2 × 384"] --> G["Linear ＋ Sigmoid：保留门 z、重置门 r"]
    X["新增事件编码：384"] --> G
    H --> N["Linear ＋ 重置门 r ＋ Tanh：候选隐状态 n"]
    X --> N
    G -->|"r"| N
    H --> M["逐元素门控相加：z × 历史 ＋ (1-z) × 候选"]
    G -->|"z"| M
    N --> M
    M --> O["新隐状态：2 × 384，传给下一事件"]
```

## 2. 奖励

- 终局收益：获胜 `+1`，失败 `-1`，`both_lose` 双方 `-1`。
- 战斗决策的过程收益按势能差计算：
  $$
  \Phi(s)=0.5\left(H_{\text{对方}}-H_{\text{己方}}\right)+0.1\frac{N_{\text{对方}}-N_{\text{己方}}}{6},\qquad
  r_t=\gamma\Phi(s_{t+1})-\Phi(s_t)
  $$
  $H$ 为队伍平均剩余血量比例，$N$ 为存活数；终局 $\Phi=0$。
- 克制命中：己方伤害技能对对方当前精灵的属性倍率大于 `1` 时，额外获得 $\min(0.01(\text{倍率}-1),0.03)$。
- 应对成功：己方技能应对类别与对方本回合行动类别一致时，额外获得 `0.02`。
- 单局克制与应对奖励合计上限 `0.1`。
- 首发决策使用终局收益；战斗决策使用过程收益，最后一个战斗决策同时使用终局收益。$\gamma=1$。
- 训练时candidate固定A侧，只记录A侧首发与战斗决策及收益；对手B侧不更新。终局停止自举。

## 3. Loss 与更新

### 3.1 己方首发与战斗

分别计算 $k\in\{L,B\}$ 的 PPO 策略 loss：

$$
L_{\pi,k}=-\mathbb E\left[\min\left(\rho_k\hat A_k,\operatorname{clip}(\rho_k,1-\epsilon,1+\epsilon)\hat A_k\right)\right],\qquad
\rho_k=\frac{\pi_k(a\mid o,h)}{\pi_{k,\mathrm{old}}(a\mid o,h)}
$$

$$
L_{\mathrm{RL}}=\sum_{k\in\{L,B\}}\left(L_{\pi,k}+0.5\,\mathbb E[(V_k-\hat G_k)^2]-\beta_k\mathbb E[H(\pi_k)]\right)
$$

- 辅助预测头读取战斗决策上下文，预测本回合造成伤害比例、承受伤害比例与双方击倒：
  $$
  L_{\mathrm{aux}}=\mathbb E\left[(d_{\mathrm{dealt}}-\hat d_{\mathrm{dealt}})^2+(d_{\mathrm{taken}}-\hat d_{\mathrm{taken}})^2\right]+\operatorname{BCE}(k,\hat k)
  $$
  标签由相邻两次战斗决策的队伍血量与存活数差计算；最后一个战斗决策不计辅助损失。
- 总损失 $L=L_{\mathrm{RL}}+0.1L_{\mathrm{aux}}$；辅助头与策略／价值共享主干。
- 首发使用 $\hat G_L=z$、$\hat A_L=z-V_{L,\mathrm{old}}$；战斗使用 GAE，$\hat G_B=\hat A_B+V_{B,\mathrm{old}}$。
- 各阶段分别平均 loss、标准化策略优势；回报目标与价值目标保持原始尺度。
- 初始设置：$\epsilon=0.2$、GAE $\lambda=0.95$、$\beta_L=\beta_B=0.01$，辅助损失系数 `0.1`，Adam 学习率 `1e-4`，梯度范数上限 `0.5`。
- PPO 按有序对局重算己方循环状态；累计策略 KL 达 `0.02` 时结束该批策略更新。

### 3.2 对手预测

$$
L_{\mathrm{opp}}=-\mathbb E[\log q_\phi(a^{\mathrm{opp}}\mid I)]
$$

- $I$ 为决策前的公开信息历史，监督标签为对方实际提交行动；训练样本使用可确认标签。
- 交叉熵更新对手编码器、公开 GRU 与候选评分器，信念与习惯通过该目标学习。
- 对手分支使用独立 Adam，初始学习率 `1e-4`，梯度范数上限 `0.5`。

### 3.3 训练组织

- 单个网络控制A、B双方，部署时两侧共享参数。训练时candidate固定为A侧并按策略采样，对手B侧为冻结模型并按argmax行动；每局双方从team0至team13随机选队。
- 对手从当前best与best池中采样：当前best占一半对局，池内按candidate对其胜率加权，优先20%–80%区间。晋升时旧best写入池，池保留最近`pool_size`个快照，胜率统计写入`pool_stats.json`。
- 每批采`games_per_batch=56`局真实terminal对局，采样按`sample_workers=16`并行，仅candidate A侧决策记入episode并计算终局收益；对手B侧只提供实际动作和预测标签。每批用candidate episode更新PPO策略／价值与辅助预测头，更新策略时冻结对手分支；再用对手动作标签更新对手交叉熵分支，更新时冻结策略／价值。默认2个PPO epoch。
- PPO按`update_chunk=16`局分块、按决策步并行重放循环状态并累积梯度；观察只用决策前可见信息；同组对手行动只作可确认候选的事后监督，补位不监督。
- 每个战斗决策记录势能、队伍血量与存活数；相邻决策计算过程收益，并生成伤害与击倒辅助标签。
- 每`eval_interval=8`批（448局训练）及最后一批对冻结best做完整评测：candidate A对best B，`eval_games=256`局、`eval_workers=32`并行，双方随机队伍。记录胜率与95% Wilson区间；胜率超过`promotion_threshold=0.55`时candidate晋升best，旧best进入池。Elo按单侧记录。评测记录写入`eval.jsonl`并生成`eval_table.md`。
- Checkpoint保存单模型、policy/opponent优化器、模型与训练配置、词表签名、best批次与单侧Elo、Python/PyTorch RNG。`metrics.jsonl`逐批保存训练指标（熵、KL、价值损失、辅助损失、对手交叉熵与`both_lose`率），评估批次另记胜率、晋升状态与best批次。

### 3.4 单步矩阵搜索

- 动作阶段使用基础分数 $B(a)$、交互矩阵 $I(a,b)$ 与对手分布 $q(b)$。取 $q$ 最高的 $k$ 个对手候选 $B_k$，候选 $a$ 的搜索分数为
  $$
  S(a)=B(a)+(1-\lambda)\sum_b q(b)I(a,b)+\lambda\min_{b\in B_k}I(a,b)
  $$
- 对 $S(a)$ 做合法候选 softmax；默认 $k=5$、$\lambda=0.5$。
- 首发与补位不搜索；搜索不调用环境状态转移或价值网络。
- 评测通过 `evaluate.py --search` 启用，训练采样仍使用原始 logits。

### 3.5 行为克隆初始化

- 规则 teacher 按克制倍率、预计伤害、速度、能量、对方上次行动类别、当前危险程度与后备预计伤害选择候选动作；对方行动类别匹配时优先应对，当前可击杀且先手时优先攻击；换人仅在危险、技能被抵抗或后备伤害明显更高时参与评分。teacher 确定性地在合法候选中取最高分。
- teacher 控制双方生成真实 terminal 对局；每个决策记录编码输入与 teacher 选中的候选索引。
- 按 side 的 episode 顺序重放循环状态，对策略 logits 做交叉熵，更新策略参数，不更新对手分支。
- 默认收集 `games=256` 局、每块 `64` 局、2 个 epoch；保存 `pretrain.pt`，训练通过 `--resume` 接续。
