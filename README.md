# audioresample

直播链路用的流式音频重采样器（线性插值 + 环形/分片缓冲），纯 Python 标准库。
输入 44.1 kHz 或 48 kHz 的 PCM 分片，输出统一为 48 kHz。

## 快速开始

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

```python
from audioresample import Resampler

r = Resampler(in_rate=44100, out_rate=48000)
r.push(pcm_chunk, timestamp_us)   # timestamp 单位：微秒
out = r.pull(480)                 # 最多返回 480 个输出样本
```

## 接口语义

### push(pcm, timestamp)

- `pcm`：输入样本序列（整数或浮点均可，内部按浮点插值）。
- `timestamp`：本分片**第一个样本的绝对时间，单位为微秒（µs）**。
  该单位与采样率无关，因此采样率切换时调用方只需沿用同一条时间轴。
  例如 48 kHz 下连续的两个 10 ms 分片可用 `0, 10000, 20000...`。
- 时间戳是权威时间：分片之间是否连续、是否丢包、是否重叠，全部由它判断。
- 采样率切换：`r.in_rate = 44100`（或 `r.set_input_rate(44100)`）。下一次
  `push` 时，缓冲中残留的旧速率样本会线性重采样到新速率，小数读取相位
  同步缩放，因此旧样本会按新速率衔接而不是被丢弃。采样率切换不需要清空
  缓冲，也不需要重新构造对象。

### pull(frames)

- 最多返回 `frames` 个输出样本；输入不足时返回更少（流结束/下溢）。
- 输出为 `int`（沿用现有接口行为）。
- 输出缓冲区的水位、相位与有效比率可通过
  `buffer_level_samples`、`buffer_level_ms`、`phase`、`effective_ratio`
  读取。

## 三类线上问题的处理

### 1. 采样率切换（爆音/错位）

- 检测 `in_rate` 变化，在下一次 `push` 时把缓冲尾部从旧速率重采样到新速率。
- `position`（缓冲内小数读取位置）按 `new_rate / old_rate` 缩放，保持相位。
- 新旧分片仍按微秒时间戳对齐；只要时间戳连续，切换点不会产生缺口。

### 2. 时间戳跳变 / 丢包（旧数据被当连续数据）

- 向前跳变超过 `gap_threshold_ms`：按时间戳计算缺失样本数，插入静音。
  `fade_ms` 内先从上一样本淡出到 0，新分片开头再淡入，避免爆音。
- 向后跳变（时间戳重叠）小于 `reset_threshold_ms`：丢弃新分片中被重复的
  前缀，保证输出时间轴单调。
- 向后跳变超过 `reset_threshold_ms`：视为新流，清空旧缓冲并重建相位。
- 缺口之后的分片严格按时间戳重新对齐，不用“接着上次样本数”的假设。

### 3. 长时间运行时钟漂移（缓冲涨满）

`pull` 内置水位伺服：

- 输入累计时长达标（已“primed”）后，预测本次 `pull` 之后的
  `buffer_level_ms`，并与 `target_level_ms` 比较（用预测值可避免一次
  分片的突发推拉把目标水位带偏）。
- 预测水位高于目标 + `drift_deadband_ms`：每个 `pull` 把有效比率增加
  `drift_step`，消耗更快，把水位拉回来。
- 预测水位低于目标 - `drift_deadband_ms`：把有效比率减小 `drift_step`，
  消耗更慢，让水位回升。
- 有效比率相对标称值的偏移被限制在 `drift_limit` 内。
- 输入不足（underrun）时不调整比率；未 primed 前也不修正，保证连续
  输入的启动输出保持精确。

## 配置参数

| 参数 | 默认值 | 含义 |
| --- | --- | --- |
| `in_rate` / `out_rate` | 48000 / 48000 | 输入 / 输出采样率；`in_rate` 可在运行中修改 |
| `target_level_ms` | 20 | 目标缓冲水位（毫秒），即期望延迟 |
| `drift_step` | 2e-5 | 每次 `pull` 的比率微调步长（相对值）；0 表示关闭漂移补偿 |
| `drift_limit` | 5e-3 | 有效比率相对标称比率的最大偏移（±0.5%） |
| `drift_deadband_ms` | 2 | 目标水位附近不做调整的死区 |
| `gap_threshold_ms` | 0.05 | 时间戳差小于该值的视为连续；用于吸收 µs 取整抖动 |
| `fade_ms` | 2 | 丢包缺口淡出/淡入长度 |
| `reset_threshold_ms` | 200 | 时间戳大幅回退时判定为新流并重置 |
| `max_buffer_ms` | 500 | 缓冲安全上限；消费者长期不 `pull` 时丢弃最旧样本 |

调参建议：

- 延迟敏感：减小 `target_level_ms`，同时保证它不小于一次 `pull` 的时长。
- 时钟漂移较大：增大 `drift_step`（收敛更快）或 `drift_limit`（允许更大
  比率范围）；`drift_deadband_ms` 越大越稳定但水位波动范围越大。
- 丢包较多：增大 `fade_ms` 降低爆音，但会增加缺口处的静音过渡长度。

## 状态序列化与恢复

`snapshot()` 返回可直接 `json.dumps` 的字典，包含速率、缓冲样本、相位、
时间戳、漂移修正量、计数器以及全部配置。`restore(state)` 可恢复到完全
一致的状态并继续推流：

```python
import json

state = json.loads(json.dumps(r.snapshot()))
r2 = Resampler()
r2.restore(state)
```

测试覆盖了快照前后继续输入/输出的一致性、JSON 往返，以及切换采样率时
尚未消费的旧样本和待处理速率变化的恢复。

## 测试覆盖

`tests/test_resampler.py` 在原有连续输入测试之外新增：

- 连续输入与上/下采样样本数；快照恢复。
- 48 kHz ↔ 44.1 kHz 切换：旧缓冲被重采样、相位缩放、边界样本对齐、
  缓冲水位断言。
- 前向丢包缺口：静音/淡出淡入、缺口后样本重新对齐、缺口样本数和水位。
- 时间戳小幅回退去重、大幅回退重置。
- 3000 次 10 ms 长跑漂移：有/无补偿的水位对比，输入输出样本计数，
  有效比率处于 `drift_limit` 内。
- 状态 JSON 往返后继续推流，输出逐样本一致，相位/水位一致。
