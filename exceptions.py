#!/usr/bin/env python3
"""
Custom Exceptions - Paper Trading System v7.0
"""


class PaperTradingException(Exception):
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self):
        return {
            "success": False,
            "error_type": self.__class__.__name__,
            "message": self.message,
            "details": self.details,
        }

    def __str__(self):
        return f"{self.message} | {self.details}" if self.details else self.message


class DatabaseError(PaperTradingException):
    pass

class PositionError(PaperTradingException):
    pass

class OrderError(PaperTradingException):
    pass

class ValidationError(PaperTradingException):
    pass

class CalculationError(PaperTradingException):
    pass


class InsufficientCapitalError(PaperTradingException):
    def __init__(self, required: float, available: float):
        super().__init__(
            f"Insufficient capital: Required Rs.{required:,.2f}, Available Rs.{available:,.2f}",
            {"required": required, "available": available, "shortfall": required - available}
        )


class PositionNotFoundError(PositionError):
    def __init__(self, position_id: int = None, symbol: str = None):
        if position_id is not None:
            super().__init__(f"Position not found: ID {position_id}", {"position_id": position_id})
        elif symbol:
            super().__init__(f"No open position for symbol: {symbol}", {"symbol": symbol})
        else:
            super().__init__("Position not found", {})


class InvalidActionError(ValidationError):
    def __init__(self, action: str, valid_actions: list):
        super().__init__(
            f"Invalid action: {action}",
            {"provided": action, "valid": valid_actions}
        )


class RateLimitError(PaperTradingException):
    def __init__(self, limit: int, window: str = "minute"):
        super().__init__(
            f"Rate limit exceeded: {limit} requests per {window}",
            {"limit": limit, "window": window}
        )


class AuthenticationError(PaperTradingException):
    def __init__(self, reason: str = "Invalid credentials"):
        super().__init__(f"Authentication failed: {reason}", {"reason": reason})


class InvalidWebhookSecretError(PaperTradingException):
    def __init__(self):
        super().__init__("Invalid webhook secret", {"hint": "Check WEBHOOK_SECRET in .env"})


class WebhookError(PaperTradingException):
    pass

class ChargesCalculationError(CalculationError):
    pass


def handle_exception(e: Exception, default_status: int = 500):
    """Convert any exception to (response_dict, http_status_code)."""
    if isinstance(e, PaperTradingException):
        status_map = {
            AuthenticationError: 401,
            InvalidWebhookSecretError: 401,
            RateLimitError: 429,
            PositionNotFoundError: 404,
            InsufficientCapitalError: 400,
            ValidationError: 400,
            OrderError: 400,
            WebhookError: 400,
            DatabaseError: 500,
            CalculationError: 500,
        }
        status_code = 400
        for exc_type, code in status_map.items():
            if isinstance(e, exc_type):
                status_code = code
                break
        return e.to_dict(), status_code
    return {
        "success": False,
        "error_type": "UnexpectedError",
        "message": str(e),
        "details": {}
    }, default_status
