# proxybudget

代理出口的流量配额模块，纯 Python 标准库。按站点和出口计量，文件锁保证
多进程原子扣减，超额自动切换出口，按天/按月对账。

## 运行

```bash
python3 demo.py
python3 -m unittest discover -s tests
```

## 额度口径

- 默认是内存模式，保持历史全局计数语义；调用 `configure(state_dir=...)`
  后启用文件模式，状态落盘到该目录的 `proxybudget_state.json`。
- 额度按 **(站点, 出口)** 计量，站点之间完全独立：`site_limits` 配置
  每站点日额度，未配置的站点用 `default_limit`，互不挤占。
- 每站点一个出口池（`proxies`，consume 中出现的出口自动入池）。
- `consume(site, proxy, amount)`：当前出口额度足够则扣减并返回
  消耗/剩余；超额时自动切到池内其他出口，返回带 `retry_after`
  （原出口到次日零点的等待秒数）的结果和切换记录，任务不中断。
- 池内所有出口都超额时才抛 `RuntimeError`，异常带 `retry_after` 属性。
- 日额度在本地零点重置；`remaining(site)` 返回站点当前出口的当日剩余。
- 扣减在独立锁文件 `proxybudget.lock` 上经 `fcntl.flock` 串行化，
  状态文件用临时文件 + `os.replace` 原子写回，多进程合计一致。

## 对账口径

- 每笔扣减实时计入当日账本（按 站点→出口 明细），跨天只重置当日计数，
  历史账本保留；`daily_report(day=None)` 返回指定日期各站点的消耗、
  剩余、分出口明细和当日切换记录。
- `monthly_report(month="YYYY-MM")` 聚合该月每日账本，输出各站点月度
  消耗、剩余（站点日额度口径，即额度减去月内总消耗）和全月切换记录。
- `save_reports()` 把当天日报、当月月报写成两个 JSON 文件并返回路径。
- 切换记录含时间、站点、原出口、目标出口、请求量、等待秒数和原因，
  日报按日期前缀筛选、月报按月份前缀筛选。
