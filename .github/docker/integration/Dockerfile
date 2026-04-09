FROM python:3.13-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    make \
    mkvtoolnix \
    bsdiff \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Node.js (for jsdom-based lyrics scraping)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp (installed via pip, not apt — apt version is usually outdated)
RUN pip install --no-cache-dir yt-dlp

# jsdom for scrape_runner.js
RUN npm install -g jsdom

WORKDIR /app
