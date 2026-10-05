# The phone exchange for the AI phone agent. Ubuntu 24.04 ships Asterisk 20 with
# AudioSocket (the module that hands call audio to app/voice_bridge.py).
FROM ubuntu:24.04
RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends asterisk asterisk-core-sounds-en-gsm \
 && rm -rf /var/lib/apt/lists/*
CMD ["asterisk", "-f", "-vvv"]
