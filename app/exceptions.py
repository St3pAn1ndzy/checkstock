"""Кастомные исключения предметной области.

Все наследуют от AppError, который мапится в HTTP-статус через
exception_handler в main.py.
"""


class AppError(Exception):
    """База для всех доменных ошибок."""

    status_code: int = 400
    code: str = "app_error"

    def __init__(self, message: str, **context):
        self.message = message
        self.context = context
        super().__init__(message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class InsufficientStockError(ConflictError):
    """Нехватка на конкретном складе при достаточном общем остатке."""

    code = "insufficient_stock"

    def __init__(self, warehouse_name: str, available: int, requested: int):
        super().__init__(
            message=f"На складе «{warehouse_name}» доступно {available}, запрошено {requested}",
            warehouse_name=warehouse_name,
            available=available,
            requested=requested,
        )


class InvalidStatusError(ConflictError):
    """Недопустимый статус движения для запрошенной операции."""

    code = "invalid_status"


class IdempotencyConflictError(ConflictError):
    """Тот же idempotency_key пришёл с другими параметрами."""

    code = "idempotency_conflict"
