from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    ENVIRONMENT: str = "development"
    SECRET_KEY: str

    # Database
    DATABASE_URL: str
    SYNC_DATABASE_URL: str
    TEST_DATABASE_URL: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 1

    # Paystack
    PAYSTACK_SECRET_KEY: str = ""
    PAYSTACK_PUBLIC_KEY: str = ""
    PAYSTACK_WEBHOOK_SECRET: str = ""

    # AWS S3
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    S3_BUCKET_NAME: str = ""

    # KYC limits (NGN, as integers)
    KYC_TIER0_DAILY_FUNDING_LIMIT: int = 10_000
    KYC_TIER1_DAILY_LIMIT: int = 50_000
    KYC_TIER2_DAILY_LIMIT: int = 500_000

    # Fraud — per-transaction limits (NGN, as integers)
    KYC_TIER1_SINGLE_LIMIT: int = 50_000
    KYC_TIER2_SINGLE_LIMIT: int = 500_000
    # Fraud — detection windows and thresholds
    FRAUD_DUPLICATE_WINDOW_SECONDS: int = 60
    FRAUD_RAPID_TRANSFER_COUNT: int = 5
    FRAUD_RAPID_TRANSFER_WINDOW_SECONDS: int = 600  # 10 minutes
    FRAUD_MERCHANT_PAYMENT_FLAG_THRESHOLD: int = 100_000

    # Payout
    MOCK_PAYOUT: bool = False

    # Celery
    CELERY_WORKER_CONCURRENCY: int = 4

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


settings = Settings()
