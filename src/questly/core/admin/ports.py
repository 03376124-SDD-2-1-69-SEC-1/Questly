from datetime import datetime
from typing import Protocol

from questly.core.admin.models import AISettings, AvailableModel, SystemStatus
from questly.core.generation.models import ProviderQuotaSnapshot


class AdminRepository(Protocol):
    def get_ai_settings(self) -> AISettings:
        """อ่านค่า AI settings ปัจจุบัน."""
        ...

    def update_ai_settings(self, settings: AISettings) -> AISettings:
        """แทนที่ AI settings ทั้งแถวด้วยค่าที่ส่งมา แล้วคืนค่าที่บันทึก."""
        ...


class GenerationUsage(Protocol):
    """What the A-01 AI tab needs from the generation slice's DraftRepository."""

    def count_all_generations(self, since: datetime) -> int:
        """Every user's generations at or after `since`."""
        ...


class ProviderQuotaReader(Protocol):
    """What the A-01 AI tab needs from the generation slice's ProviderQuota."""

    def snapshot(self) -> ProviderQuotaSnapshot:
        """What the model provider says is left today; never raises."""
        ...


class ModelCatalog(Protocol):
    """The generation models the provider offers right now, in its own order."""

    def available_models(self) -> tuple[AvailableModel, ...]:
        """Never raises; empty when the provider cannot be reached."""
        ...


class SystemHealthChecker(Protocol):
    def check_services_health(self) -> SystemStatus:
        """ตรวจสอบสถานะบริการทั้ง 6 ตัว."""
        ...
