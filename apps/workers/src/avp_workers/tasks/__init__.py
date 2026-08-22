"""Celery tasks.

The Phase 1 pipeline stages (product-spec.md §5.4) are implemented from Epic 2
onward. Epic 1 provides only the queue and a health task that proves it works
end to end.
"""

from . import health

__all__ = ["health"]
