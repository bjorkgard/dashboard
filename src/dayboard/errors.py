class ConfigError(ValueError):
    """Invalid nonsecret preferences; never include raw configuration in errors."""
