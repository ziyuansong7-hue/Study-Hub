from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import TodoItem

VALID_PRIORITIES = {"low", "normal", "high"}
VALID_CATEGORIES = {"personal", "interview", "project", "application", "appointment", "other"}


def _local_now() -> datetime:
    return datetime.now().replace(microsecond=0)


class TodoService:
    def __init__(self, session: Session, student_id: str = "default"):
        self.session = session
        self.student_id = student_id

    def _item(self, item_id: int) -> TodoItem:
        item = self.session.scalar(select(TodoItem).where(
            TodoItem.id == item_id, TodoItem.student_id == self.student_id
        ))
        if not item:
            raise ValueError("todo item not found")
        return item

    @staticmethod
    def _validate(priority: str, category: str) -> None:
        if priority not in VALID_PRIORITIES:
            raise ValueError("invalid todo priority")
        if category not in VALID_CATEGORIES:
            raise ValueError("invalid todo category")

    def create(
        self,
        *,
        title: str,
        due_at: datetime | None = None,
        notes: str = "",
        priority: str = "normal",
        category: str = "personal",
    ) -> TodoItem:
        title = title.strip()
        if not title:
            raise ValueError("todo title is required")
        self._validate(priority, category)
        item = TodoItem(
            student_id=self.student_id,
            title=title[:255],
            notes=notes.strip()[:2000],
            due_date=due_at.date() if due_at else date.today(),
            due_at=due_at,
            due_kind="datetime" if due_at else "none",
            priority=priority,
            category=category,
        )
        self.session.add(item)
        self.session.flush()
        return item

    def edit(
        self,
        item_id: int,
        *,
        title: str,
        due_at: datetime | None,
        notes: str,
        priority: str,
        category: str,
    ) -> TodoItem:
        item = self._item(item_id)
        title = title.strip()
        if not title:
            raise ValueError("todo title is required")
        self._validate(priority, category)
        item.title = title[:255]
        item.notes = notes.strip()[:2000]
        item.due_at = due_at
        item.due_date = due_at.date() if due_at else date.today()
        item.due_kind = "datetime" if due_at else "none"
        item.priority = priority
        item.category = category
        self.session.flush()
        return item

    def grouped(self) -> dict[str, list[TodoItem]]:
        now = _local_now()
        items = list(self.session.scalars(select(TodoItem).where(
            TodoItem.student_id == self.student_id,
            TodoItem.status != "dismissed",
        ).order_by(TodoItem.status, TodoItem.due_at, TodoItem.created_at)))
        result: dict[str, list[TodoItem]] = {
            "inbox": [], "overdue": [], "today": [], "upcoming": [], "completed": [], "all": items,
        }
        for item in items:
            if item.status == "completed":
                result["completed"].append(item)
            elif item.due_kind == "none" or item.due_at is None:
                result["inbox"].append(item)
            elif item.due_at < now:
                result["overdue"].append(item)
            elif item.due_at.date() == now.date():
                result["today"].append(item)
            else:
                result["upcoming"].append(item)
        priority_order = {"high": 0, "normal": 1, "low": 2}
        for key in ("inbox", "overdue", "today", "upcoming"):
            result[key].sort(key=lambda item: (priority_order.get(item.priority, 1), item.due_at or datetime.max))
        result["completed"].sort(key=lambda item: item.completed_at or item.updated_at, reverse=True)
        return result

    def list_due(self) -> list[TodoItem]:
        groups = self.grouped()
        return groups["overdue"] + groups["today"]

    def action(self, item_id: int, action: str) -> TodoItem:
        item = self._item(item_id)
        if action == "complete":
            item.status = "completed"
            item.completed_at = datetime.now(timezone.utc)
        elif action == "reopen":
            item.status = "pending"
            item.completed_at = None
        elif action == "postpone":
            base = item.due_at or datetime.combine(date.today(), time(hour=23, minute=59))
            item.due_at = max(
                base + timedelta(days=1),
                datetime.combine(date.today() + timedelta(days=1), base.time()),
            )
            item.due_date = item.due_at.date()
            item.due_kind = "datetime"
            item.status = "pending"
        elif action == "delete":
            item.status = "dismissed"
        else:
            raise ValueError("unknown todo action")
        self.session.flush()
        return item
