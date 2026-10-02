# Build context: upstream/web (patched Cloud-API-Demo-Web), copied there by setup.sh
FROM node:16.20.2-bullseye AS build
WORKDIR /src
ARG VITE_DJI_APP_ID
ARG VITE_DJI_APP_KEY
ARG VITE_DJI_APP_LICENSE
ARG VITE_BASE_URL
ARG VITE_WS_URL
ARG VITE_MAPPING_URL
ARG VITE_FARMING_URL
ARG VITE_RTMP_URL
ARG VITE_WHEP_BASE
ARG VITE_HLS_BASE
ARG VITE_RTSP_USER
ARG VITE_RTSP_PASSWORD
ARG VITE_MAP_CENTER
ARG VITE_MAP_ZOOM
ARG VITE_MAP_TILE_URL
COPY package.json yarn.lock ./
RUN yarn install --frozen-lockfile --ignore-engines --network-timeout 300000
COPY . .
RUN yarn build

FROM nginx:1.27.5-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /src/dist /usr/share/nginx/html
EXPOSE 80
