# filefetch

纯标准库附件下载：download(url, dest, open_url=None, checksum=None, limiter=None)，返回落盘字节数。

## 下载流程

- 先探测 Range（bytes=0-0）：206 或 Accept-Ranges: bytes 视为支持，按 .part 已下载字节续传；不支持或探测失败则记录日志并整包重下；站点并发占满时跳过探测，主请求拿到许可后按响应状态决定。
- dest 是目录时，最终文件名为 <内容sha256><URL原扩展名>，重名自动复用不覆盖；dest 是文件路径时直接写入该路径。
- 数据先落 <目标>.part 或目录内 <url哈希>.part，完成后校验 Content-Length 与可选 checksum=("sha256", "hex")，通过才 os.replace 原子入库。
- 长度或校验和不一致抛 VerificationError，.part 原样保留（不改名、不删除），修复后重试可继续续传。
- SiteLimiter(per_host=2) 按主机名限流，默认全局每站点并发 2，不同站点互不阻塞。

```bash
python3 demo.py          # 本地服务模拟断连 -> 保留 .part -> 续传校验
python3 -m unittest discover -s tests
```
