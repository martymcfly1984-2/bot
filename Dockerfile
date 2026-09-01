yamlversion: '3.8'

services:
  python_bot:
    build: .
    environment:
      - ENGINE_URL=ws://engine:8081/ws
      - BOT_ID=player1
      - BOT_ICON=🐍
    networks:
      - alchemy-net
    restart: on-failure

networks:
  alchemy-net:
    external: true