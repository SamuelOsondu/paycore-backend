import uuid

import pytest
import pytest_asyncio

from app.core.exceptions import ConflictError, NotFoundError
from app.models.user import UserRole
from app.services.user import UserService


@pytest.mark.asyncio
async def test_get_profile_returns_user(db_session, test_user):
    service = UserService(db_session)
    user = await service.get_profile(test_user.id)
    assert user.id == test_user.id
    assert user.email == test_user.email


@pytest.mark.asyncio
async def test_get_profile_not_found(db_session):
    service = UserService(db_session)
    with pytest.raises(NotFoundError):
        await service.get_profile(uuid.uuid4())


@pytest.mark.asyncio
async def test_update_profile_full_name(db_session, test_user):
    service = UserService(db_session)
    updated = await service.update_profile(test_user.id, full_name="New Name")
    assert updated.full_name == "New Name"


@pytest.mark.asyncio
async def test_update_profile_phone(db_session, test_user):
    service = UserService(db_session)
    updated = await service.update_profile(test_user.id, phone="+2348012345678")
    assert updated.phone == "+2348012345678"


@pytest.mark.asyncio
async def test_update_profile_phone_conflict(db_session, test_user):
    """Phone already taken by another user raises ConflictError."""
    from app.models.user import User
    import uuid as _uuid

    other = User(
        id=_uuid.uuid4(),
        email="other@example.com",
        hashed_password="x",
        full_name="Other",
        phone="+2348099999999",
        role=UserRole.USER,
    )
    db_session.add(other)
    await db_session.flush()

    service = UserService(db_session)
    with pytest.raises(ConflictError):
        await service.update_profile(test_user.id, phone="+2348099999999")


@pytest.mark.asyncio
async def test_update_profile_cannot_change_role(db_session, test_user):
    """
    UserService.update_profile accepts only full_name and phone.
    The role field is not in the signature — this is the enforcement.
    """
    service = UserService(db_session)
    # Role stays unchanged after any profile update
    updated = await service.update_profile(test_user.id, full_name="Updated Name")
    assert updated.role == UserRole.USER


@pytest.mark.asyncio
async def test_update_kyc_tier(db_session, test_user):
    service = UserService(db_session)
    updated = await service.update_kyc_tier(test_user.id, tier=1)
    assert updated.kyc_tier == 1


@pytest.mark.asyncio
async def test_soft_deleted_user_not_found(db_session, test_user):
    """Soft-deleted users must not be returned by the repository."""
    from app.repositories.user import UserRepository

    repo = UserRepository(db_session)
    await repo.soft_delete(test_user)

    service = UserService(db_session)
    with pytest.raises(NotFoundError):
        await service.get_profile(test_user.id)
