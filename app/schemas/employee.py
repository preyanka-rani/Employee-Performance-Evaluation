"""
app/schemas/employee.py
────────────────────────
Pydantic v2 schemas for Employee CRUD operations.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class EmployeeBase(BaseModel):
    employee_id: str = Field(..., min_length=1, max_length=20, examples=["20230001"])
    name: str = Field(..., min_length=1, max_length=200)
    email: EmailStr
    team: str = Field(..., min_length=1, max_length=50, examples=["developer"])
    gitlab_username: str | None = Field(None, max_length=100)
    is_active: bool = True


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeUpdate(BaseModel):
    name: str | None = None
    team: str | None = None
    gitlab_username: str | None = None
    is_active: bool | None = None


class EmployeeResponse(EmployeeBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
