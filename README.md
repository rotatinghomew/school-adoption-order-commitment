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

## 选用后端（教务与采购共用）

`service/` 在同一份后端上实现教务选用与采购报订，所有时间窗口按半开区间
`[begin, end)` 判断，运行期记录与已签收承诺独立保存，重算选用清单不会
抹掉已承诺册数。模块职责：

| 模块 | 职责 |
| --- | --- |
| `repository.py` | 县教务接口六类资料的只读目录与引用校验 |
| `state.py` | 申报、确认、异议、裁决、待核调动、切换、暂停、后备与承诺行 |
| `districts.py` | 片区归属、共建片区、生效例外与授权优先级（例外优先于共建选择） |
| `schemes.py` | 去重去例外后的方案范围、范围签名、确认/异议、`minimum` 与 `distinct_centers` 门槛 |
| `decisions.py` | 班级终选、冲突裁决、已开课原书延续、切换课次与后备转用 |
| `procurement.py` | 唯一终选报订、容量占位、减单改货、后备只覆盖未承诺册数 |
| `exchange.py` | 合作方交换契约客户端（断连凭原操作号轮询）与测试假伙伴 |
| `api.py` | 教务与采购共用的 HTTP 接口 |

关键口径：

* 一个班同一科目只能有一个正式承诺书种；冲突方案在县教务裁决前
  `committable_book` 为空，报订回 409；
* 方案确认绑定范围签名，范围变化（新例外、书目暂停、调动）后旧确认
  列入 `stale_confirmations`，不能替该中心校的班级继续作决定；
* 已开教学班在授权变化后 `in_use_book` 保留原书，待县教务确认
  `switch_lesson` 且课次到达后才换书；
* 减单/改货走 `adjust` 交换契约，结果未知时原册数继续占用并在
  `pending_adjustments` 解释，轮询到完成回执后才改写数量；
* 后备承诺与正式承诺分行，合计不超过班级人数，避免同一班到两套书。

启动 HTTP 后端（可选对接合作方伙伴）：

```bash
python3 partner/server.py --port 8087
python3 service/api.py --port 8088 --data-dir data --partner-url http://127.0.0.1:8087
```

主要端点（收发 UTF-8 JSON，日期参数为 ISO 字符串）：

```text
POST /applications                      中心校申报（同班同学期重复申报回 409）
POST /exceptions                        运行期纳入校点个别授权
GET  /classrooms/{id}?day=...           学校查当前按什么书开课与待核范围
GET  /classrooms/{id}/authority         谁能为该班该课作选择及依据
GET  /classrooms/{id}/membership        当前片区归属与调动待核
GET  /schemes/{id}/scope?day=...        去重去例外后的适用范围与门槛
POST /schemes/{id}/confirmations        中心校确认（绑定范围签名）
POST /schemes/{id}/objections           有理由异议，旧确认对该中心失效
POST /classrooms/{id}/arbitration       县教务裁决冲突选择
POST /classrooms/{id}/pending-moves     期中调动待核（不改动现有归属）
POST /classrooms/{id}/switches          确认切换课次
POST /classrooms/{id}/fallback          未承诺且未开课班转后备
POST /books/{id}/suspensions            书目暂停
POST /procurement/reservations          正式报订承诺
POST /procurement/adjustments           按交换契约减单/改货
POST /procurement/fallback              后备只对未承诺册数出单
GET  /procurement/classrooms/{id}       按班查有效数量、后备数量与待调整原因
GET  /procurement/operations/{id}       轮询合作方回执
```
