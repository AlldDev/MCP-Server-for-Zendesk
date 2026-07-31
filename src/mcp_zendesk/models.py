from __future__ import annotations

from typing import Literal

TicketStatus = Literal["new", "open", "pending", "hold", "solved", "closed"]
TicketPriority = Literal["low", "normal", "high", "urgent"]
