from pydantic import BaseModel, ConfigDict


class HealthRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
