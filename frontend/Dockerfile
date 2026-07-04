# Stage 1: Build the React application
FROM node:18-alpine AS builder

WORKDIR /app

# Copy workspace root files for workspace dependency resolution
COPY package.json package-lock.json ./

# Copy ALL workspace packages (required for npm to resolve workspace deps)
COPY packages ./packages

# Copy frontend package files
COPY frontend/package.json frontend/package-lock.json ./frontend/

# Copy frontend source code
COPY frontend ./frontend

# Install dependencies (npm needs all workspace packages to resolve workspace deps)
RUN npm install

# Build the frontend application
WORKDIR /app/frontend
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine

# Copy built files from builder stage
COPY --from=builder /app/frontend/dist /usr/share/nginx/html

# Copy nginx configuration
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]