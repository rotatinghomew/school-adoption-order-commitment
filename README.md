# 联办教学点的教材选用与订货承诺

本项目资料用于跨单位交接演练。记录和名称均为合成，时间、角色与对象编号在同一份资料内保持原始对应。

## 资料格式

`data/` 按来源分存 UTF-8 JSON 对象数组，`data/format.json` 描述各来源字段、引用和单号作用域。对象中的空值表示材料未提供相应事实，具体含义见后文。

班级标识跨共建片区唯一，课堂归属以 districts 的半开区间确定。schemes 的 minimum 是尚能按此方案生效的不同班级数，distinct_centers 是参与有效确认的不同中心校数，任一班只计一次。兼有两个仍有效且无个别例外的共同选择、又尚未确定优先时，县教务应裁决，裁决前不得承诺两份相互冲突的订货。switch_lesson 是可以请求县教务确认的最早课次，空值表示本学期未声明可切换课次。采购行可分别接受、修改或取消，不能仅凭发出请求变更外部已确认数量。

## 合作方接口
启动本地伙伴服务
```bash
python3 partner/server.py --port 8087
```
受理使用 `POST /order-lines`，查询使用 `GET /order-lines/{operation_id}`。输入 UTF-8 JSON，`operation_id` 是调用方提供的操作号，`action` 及其必填字段见 `partner/exchange.json`。字段空间只供本地演练，不对应任何线上账户。
首次受理回 202，回执可先显示 `accepted`，演练伙伴在后续查询返回 `completed`、`receipt_id` 及原受理单。相同操作号和相同输入可再次受理，内容不符回 409。查询未知操作号回 404。用 `--disconnect-after-accept` 可模拟合作方收到新单后连接中断。伙伴进程重启会清空演练回执。

## 本地使用

使用 Python 3.11.2 标准库。检查样例数据及随附的本地接口契约时，在本项目根目录运行：

```bash
python3 -m unittest discover -s tests -v
```
