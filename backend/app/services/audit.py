from sqlalchemy.orm import Session
from app.models.entities import ActivityLog

def log(db: Session, action: str, entity_type: str, entity_id: str|None=None, company_id: str|None=None, user_id: str|None=None, metadata: dict|None=None):
    item = ActivityLog(action=action, entity_type=entity_type, entity_id=entity_id, company_id=company_id, user_id=user_id, metadata_json=metadata or {})
    db.add(item)
    return item
