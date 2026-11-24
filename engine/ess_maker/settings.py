"""Settings stub: no Azure OpenAI, so the drafter uses its deterministic wording."""


class _Settings:
    azure_openai_endpoint = ""
    azure_openai_api_key = ""

    def __getattr__(self, name):
        return ""


settings = _Settings()
