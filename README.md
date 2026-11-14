# formstate

现状：可重入的多步表单状态机，接口为 start(form) 和 step(state, values)。

已解决：重复提交幂等返回，失败回退一步并保留已填值，验证码挂起等回填，状态可序列化断点续跑。

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 状态口径

- state 是纯 JSON 可序列化 dict：form/spec/step（调用计数）/current（当前步骤下标）/values/status/pending/results/attempts/trail。
- status：active → suspended（验证码挂起）→ active；提交失败回退一步但保持 active；验证码超时置 failed；全部步骤完成置 done。
- 重启恢复：loads(dumps(state)) 或 json.loads(json.dumps(state))，从 current 断点继续。
- form 可传名字（旧用法，步骤按需自动生成）或 {"name", "steps"} 规格；步骤支持 page、type="captcha"、timeout、fail_times。

## 幂等口径

- 幂等键 = sha256(form 名 + 本次 values 的规范化 JSON) 前 16 位；同键重复提交直接返回已有结果，不再提交。
- 命中记 trail status="idempotent"（hit=True），并把 current 快进到该结果之后，用于回退后重放。
- 每步轨迹记录步骤名、页面指纹 sha256(page) 前 16 位和幂等键。
- 失败轨迹记 fallback_reason 和 fallback_to；已填 values 保留在 state 中。

