# Chrome and its system libraries come from the official puppeteer image; the
# pinned puppeteer below matches its bundled Chrome, so nothing is downloaded.
FROM ghcr.io/puppeteer/puppeteer:24.43.1
ENV PUPPETEER_SKIP_DOWNLOAD=true NODE_ENV=production
WORKDIR /home/pptruser/app
COPY --chown=pptruser:pptruser package.json package-lock.json ./
RUN npm ci --omit=dev --ignore-scripts && npm cache clean --force
COPY --chown=pptruser:pptruser server.mjs leumi.mjs cards.mjs normalize.mjs ./
EXPOSE 8095
CMD ["node", "server.mjs"]
