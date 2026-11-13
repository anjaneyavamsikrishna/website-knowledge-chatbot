"""Domain errors that map cleanly onto HTTP status codes."""


class AppError(Exception):
    status_code = 500
    detail = "Internal error"

    def __init__(self, detail: str | None = None):
        super().__init__(detail or self.detail)
        self.detail = detail or self.detail


class KnowledgeBaseEmpty(AppError):
    status_code = 503
    detail = "The knowledge base is empty. Run the ingestion first."


class LLMUnavailable(AppError):
    status_code = 503
    detail = "The language model is not available right now. Please try again shortly."


class RefreshInProgress(AppError):
    status_code = 409
    detail = "A knowledge base refresh is already running."


class LLMError(Exception):
    """Raised by LLM clients for any provider failure or unusable output."""


class ProviderFatalError(Exception):
    """Non-retryable provider problem (no credits/quota, invalid key, no access).
    Retrying or continuing with other pages is pointless, so callers abort immediately."""


class EmbeddingError(Exception):
    """Transient embedding failure that persisted after retries."""
