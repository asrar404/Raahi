"""User profile and emergency contact schemas."""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# E.164-ish: optional +, 8-15 digits. Deliberately permissive — Indian
# numbers arrive as +919876543210, 919876543210 and 9876543210 in the wild,
# and rejecting an emergency contact over formatting is the wrong trade.
PHONE_RE = re.compile(r"^\+?[1-9]\d{7,14}$")


def _normalise_phone(value: str) -> str:
    """Strip spaces, dashes and brackets, then validate."""
    cleaned = re.sub(r"[\s\-()]", "", value or "")
    if not PHONE_RE.match(cleaned):
        raise ValueError(f"invalid phone number: {value!r}")
    return cleaned


class EmergencyContact(BaseModel):
    """One person to alert when SOS fires."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(description="E.164 format preferred, e.g. +919876543210")
    relation: Optional[str] = Field(default=None, max_length=40)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: str) -> str:
        return _normalise_phone(v)


class EmergencyContactsUpdate(BaseModel):
    """Full replacement of the contact list.

    Capped at 5: Twilio escalation fans out to all of them, and beyond a
    handful the alert stops being actionable.
    """

    contacts: List[EmergencyContact] = Field(max_length=5)


class UserProfile(BaseModel):
    """User as returned by /auth/me."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    supabase_uid: str
    full_name: str
    phone: str
    email: Optional[str] = None
    gender: Optional[str] = None
    preferred_modes: List[str] = Field(default_factory=list)
    budget_default: float = 500.0
    emergency_contacts: List[EmergencyContact] = Field(default_factory=list)
    sos_enabled: bool = True
    home_city: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class UserProfileUpdate(BaseModel):
    """Partial profile update — omitted fields are left untouched."""

    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    gender: Optional[str] = None
    preferred_modes: Optional[List[str]] = None
    budget_default: Optional[float] = Field(default=None, ge=0)
    sos_enabled: Optional[bool] = None
    home_city: Optional[str] = None

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: Optional[str]) -> Optional[str]:
        return _normalise_phone(v) if v else v

    @field_validator("gender")
    @classmethod
    def _check_gender(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        allowed = {"female", "male", "other"}
        lowered = v.lower()
        if lowered not in allowed:
            raise ValueError(f"gender must be one of {sorted(allowed)}")
        return lowered

    @field_validator("preferred_modes")
    @classmethod
    def _check_modes(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        allowed = {"walk", "metro", "bus", "train", "auto", "cab", "rapido", "ferry"}
        invalid = [m for m in v if m not in allowed]
        if invalid:
            raise ValueError(f"unsupported modes: {invalid}")
        return v


class AuthVerifyRequest(BaseModel):
    """Profile details supplied on first login.

    `users.phone` is NOT NULL and drives SOS callbacks, so it must be present
    the first time a user is provisioned. Supabase supplies it automatically
    for phone-OTP signups; email/OAuth signups have to pass it here.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    gender: Optional[str] = None
    home_city: Optional[str] = None
    budget_default: Optional[float] = Field(default=None, ge=0)

    @field_validator("phone")
    @classmethod
    def _check_phone(cls, v: Optional[str]) -> Optional[str]:
        return _normalise_phone(v) if v else v


class AuthVerifyResponse(BaseModel):
    """Result of exchanging a Supabase JWT for a RAAHI profile."""

    user: UserProfile
    created: bool = Field(description="True when this call provisioned the user")
