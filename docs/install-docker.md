[Back to README](../README.md)

## Install (Docker Option - thanks @JDare)

### Single Execution (One-time price check)
For a single price check without scheduling:
```bash
docker run --rm \
  -v ./config.yaml:/app/config.yaml:ro \
  ghcr.io/jdeath/checkroyalcaribbeanprice:latest \
  check
```

### Scheduled Execution
#### Option 1: Using Pre-built Image
1. Create a `docker-compose.yml` file:
```yaml
services:
  cruise-price-checker:
    image: ghcr.io/jdeath/checkroyalcaribbeanprice:latest
    container_name: cruise-price-checker
    restart: unless-stopped
    environment:
      # Timezone for cron execution (default: UTC)
      # Examples: America/New_York, America/Chicago, America/Los_Angeles, Europe/London
      # Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
      - TZ=America/New_York
      # Cron schedule: 7 AM and 7 PM daily in the specified timezone
      - CRON_SCHEDULE=0 7,19 * * *
    volumes:
      # Mount your config file
      - ./config.yaml:/app/config.yaml:ro
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```
2. Create your `config.yaml` file (see "[Edit Config File](config.md)" section)
3. Run: `docker compose up -d`

#### Option 2: Build from Source
1. Clone this repository: `git clone https://github.com/jdeath/CheckRoyalCaribbeanPrice.git`
2. `cd CheckRoyalCaribbeanPrice`
3. Create your `config.yaml` file (see "[Edit Config File](config.md)" section)
4. Run: `docker compose up -d`

The Docker container will run the price checker on the schedule you have defined.
