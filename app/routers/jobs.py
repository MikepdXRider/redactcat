from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Job, JobEntity, User
from app.schemas import EntityRead, JobCreate, JobRead, RedactionResult, RedactionSubmit
from app.services.detection import detect_pii_entities
from app.services.redaction import apply_text_redactions

router = APIRouter(tags=["jobs"])


@router.post("/text", response_model=JobRead, status_code=status.HTTP_201_CREATED)
def create_text_job(
    body: JobCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Job:
    job = Job(user_id=current_user.id, input_text=body.text)
    db.add(job)
    db.flush()

    for entity in detect_pii_entities(body.text, job.id):
        db.add(entity)

    db.commit()

    return db.scalar(select(Job).where(Job.id == job.id).options(joinedload(Job.entities)))


@router.get("/{job_id}/entities", response_model=list[EntityRead])
def get_job_entities(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[JobEntity]:
    job = db.get(Job, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return list(db.scalars(select(JobEntity).where(JobEntity.job_id == job_id)).all())


@router.post("/{job_id}/redact", response_model=RedactionResult)
def redact_job(
    job_id: int,
    body: RedactionSubmit,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    job = db.get(Job, job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    entities = list(
        db.scalars(
            select(JobEntity).where(
                JobEntity.job_id == job_id,
                JobEntity.id.in_(body.entity_ids),
            )
        ).all()
    )

    redacted_text = apply_text_redactions(job.input_text, entities)

    db.execute(delete(JobEntity).where(JobEntity.job_id == job_id))
    db.delete(job)
    db.commit()

    return {"redacted_text": redacted_text}
