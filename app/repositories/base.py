from typing import Generic, TypeVar
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    """
    Thin base providing the session reference.
    Subclasses own their queries completely — no magic filtering here.
    Soft-delete filtering is explicit in each subclass method.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def flush(self) -> None:
        await self.session.flush()

    async def refresh(self, instance: ModelT) -> None:
        await self.session.refresh(instance)
