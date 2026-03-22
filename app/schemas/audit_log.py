import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.audit_log import ActorType


class AuditLogOut(BaseModel):
    """
    Public representation of an audit log entry (admin-only).

    The SQLAlchemy model stores metadata in ``metadata_`` (Python attribute)
    to avoid shadowing SQLAlchemy's reserved ``metadata`` name on Base.
    The ``validation_alias`` maps the Python attribute to the JSON field name.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    actor_id: Optional[uuid.UUID]
    actor_type: ActorType
    action: str
    target_type: Optional[str]
    target_id: Optional[uuid.UUID]
    # validation_alias reads obj.metadata_ when constructing from ORM instance
    metadata: Optional[dict[str, Any]] = Field(None, validation_alias="metadata_")
    ip_address: Optional[str]
    created_at: datetime
