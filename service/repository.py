"""县教务接口资料的装载与只读目录。

数据来源对应题述的县教务接口事实：

* ``classrooms``：班级、科目、人数、开课日、最早可切换课次；
* ``districts``：班级对中心校的半开归属区间，支持期中调整与共建片区；
* ``choices``：各课可用书目及其有效期，``fallback`` 标记后备书目；
* ``schemes``：中心校共同方案的申报范围与生效门槛；
* ``exceptions``：校点个别授权的申请与审核记录；
* ``centers``：中心校名册。

目录类只负责按事实索引，不做带时间判断的业务计算，业务规则放在
:mod:`service.districts`、:mod:`service.schemes`、:mod:`service.decisions`。
"""

import json
from pathlib import Path

from .errors import NotFound, ValidationError

APPROVED_DECISION = "审核准许"


class Catalog:
    """对六类资料做不可变索引的只读目录。"""

    def __init__(self, documents):
        self._tables = dict(documents)
        self._index = {}
        for name, rows in self._tables.items():
            if not isinstance(rows, list):
                raise ValidationError(f"资料 {name} 必须是对象数组")
            self._index[name] = {row["id"]: row for row in rows}
        self._check_references()

    # -- 装载 -----------------------------------------------------------

    @classmethod
    def load(cls, data_dir):
        """从包含各来源 JSON 的目录装载。"""
        data_dir = Path(data_dir)
        documents = {}
        for path in sorted(data_dir.glob("*.json")):
            if path.stem == "format":
                continue
            documents[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        return cls(documents)

    def _check_references(self):
        references = [
            ("districts", "center", "centers"),
            ("districts", "classroom", "classrooms"),
            ("schemes", "centers", "centers"),
            ("schemes", "classes", "classrooms"),
            ("schemes", "book", "choices"),
            ("exceptions", "book", "choices"),
        ]
        for source, field, target in references:
            target_ids = self._index.get(target, {})
            for row in self._tables.get(source, []):
                values = row[field]
                if not isinstance(values, list):
                    values = [values]
                for value in values:
                    if value is not None and value not in target_ids:
                        raise ValidationError(
                            f"{source}.{field} 引用了不存在的 {target}: {value}",
                            details={"source": source, "id": row["id"]},
                        )

    # -- 通用访问 -------------------------------------------------------

    def table(self, name):
        if name not in self._tables:
            raise NotFound(f"资料来源不存在: {name}")
        return list(self._tables[name])

    def get(self, name, record_id):
        table = self._index.get(name)
        if table is None:
            raise NotFound(f"资料来源不存在: {name}")
        row = table.get(record_id)
        if row is None:
            raise NotFound(f"{name} 中没有编号 {record_id} 的记录")
        return row

    def require(self, name, record_id):
        return self.get(name, record_id)

    # -- 领域便捷访问 ----------------------------------------------------

    def classroom(self, classroom_id):
        return self.require("classrooms", classroom_id)

    def choice(self, book_id):
        return self.require("choices", book_id)

    def districts_for(self, classroom_id):
        return [
            row
            for row in self._tables["districts"]
            if row["classroom"] == classroom_id
        ]

    def exceptions_for(self, classroom_id):
        return [
            row
            for row in self._tables["exceptions"]
            if row["classroom"] == classroom_id
        ]

    def choices_for_subject(self, subject):
        return [row for row in self._tables["choices"] if row["subject"] == subject]

    def fallback_choice(self, subject):
        matches = [
            row
            for row in self.choices_for_subject(subject)
            if row.get("fallback")
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ValidationError(f"科目 {subject} 存在多个后备书目")
        return matches[0]

    def scheme(self, scheme_id):
        return self.require("schemes", scheme_id)

    def schemes(self):
        return list(self._tables["schemes"])
