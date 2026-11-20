"""Transport for usage events: line protocol, the InfluxDB client, lookups.

Nothing in this package imports Django.
"""

# Every usage log line goes to this logger, which Django LOGGING configures at
# INFO. No line on it carries a network address, a credential or an event.
USAGE_LOGGER_NAME = "astrodash.usage"
