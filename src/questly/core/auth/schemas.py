"""HTTP contracts for /api/v1/auth."""

from pydantic import BaseModel, Field, field_validator


class SignUpRequest(BaseModel):
    full_name: str = Field(max_length=120)
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    confirm_password: str = Field(max_length=256)
    wants_instructor: bool = False
    faculty: str = Field(default="", max_length=120)
    # ADR-0013: an address outside @kmitl.ac.th, Student only
    external: bool = False

    @field_validator("full_name")
    @classmethod
    def full_name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("full_name must not be blank")
        return value


class VerifyRequest(BaseModel):
    token: str = Field(max_length=256)


class ResendRequest(BaseModel):
    email: str = Field(max_length=254)


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


class InstructorRequestCreate(BaseModel):
    faculty: str = Field(max_length=120)


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    email_verified: bool
    student_number: str | None


class LoginResponse(BaseModel):
    user: UserResponse
    landing: str


class InstructorRequestResponse(BaseModel):
    id: int
    user_id: int
    faculty: str
    status: str
