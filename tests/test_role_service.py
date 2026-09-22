from app.database.connection import connect
from app.database.repository import Repository
from app.services.role_service import RoleService

def test_creator_cannot_delete_creator():
    r=Repository(connect(':memory:'));s=RoleService(r);r.upsert_user(1,'A','');r.upsert_user(2,'B','');r.set_role_if_current(1,'user','creator');r.set_role_if_current(2,'user','creator');assert not s.can_manage(1,2)
