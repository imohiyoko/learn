# learn をブラウザから使うためのコンテナ。学習データはホストのこのフォルダに残る（compose でマウント）。
FROM node:22-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 poppler-utils git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && npm install -g @anthropic-ai/claude-code \
 && npm cache clean --force

ENV HOME=/home/node \
    LEARN_HOST=0.0.0.0 \
    LEARN_PORT=8765
USER node
WORKDIR /learn
EXPOSE 8765
CMD ["python3", "webui/server.py"]
