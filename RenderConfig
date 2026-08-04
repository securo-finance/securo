# 🚀 Securo — Render Deployment Guide (PaaS)

This guide provides step-by-step instructions for deploying the **Securo** application on the **Render** platform using Docker containers (Frontend and Backend) and a Redis Key-Value instance.

---
## 🏗️ Deployment Architecture

---

## 🛠️ Step 1: Redis Instance (Key Value)

The Securo backend requires a Redis instance for caching and session management.

1. In the Render dashboard, click the **`+ New`** button in the top-right corner.
2. Select the **`Key Value`** option (Render's managed Redis service).
3. Set an instance name (e.g., `securo-redis`).
4. Select the **Free** plan and click **Create Key Value**.
5. Once created, copy the **Internal Connection String** (formatted as `redis://...`). You will use this as the `REDIS_URL` environment variable for the backend.

---

## ⚙️ Step 2: Backend Configuration (Web Service)

Create a new **Web Service** connected to your GitHub repository.

### 2.1 Build Settings (Context Directory)
Adjust the build paths so Render targets the `/backend` directory:

* **Dockerfile Path:** `./backend/Dockerfile`
* **Docker Build Context Directory:** `./backend/`

### 2.2 Startup Command (Docker Command)
Set the custom startup command:

* **Docker Command:** `bash ./start.sh`

### 2.3 Environment Variables (`Environment Variables`)

Add the following variables under the **Environment** tab of your service:

| Key | Description / Example |
| :--- | :--- |
| `BACKEND_URL` | `https://securo-oic9.onrender.com` *(Public URL of your backend on Render)* |
| `FRONTEND_URL` | `https://securo-1-iy2i.onrender.com` *(Public URL of your frontend on Render)* |
| `DATABASE_URL` | `postgresql+asyncpg://user:password@host:5432/db` *(Postgres/Supabase connection string)* |
| `REDIS_URL` | `redis://...` *(Connection string obtained in Step 1)* |
| `SECRET_KEY` | `YourRandomSecretKey` |
| `OIDC_ENABLED` | `true` |
| `OIDC_PROVIDER_NAME` | `Google` |
| `OIDC_CLIENT_ID` | `your-client-id.apps.googleusercontent.com` |
| `OIDC_CLIENT_SECRET` | `your-google-client-secret` |
| `OIDC_DISCOVERY_URL` | `https://accounts.google.com/.well-known/openid-configuration` |
| `OIDC_AUTO_REGISTER` | `true` |
| `OIDC_REQUIRE_VERIFIED_EMAIL` | `true` |
| `OIDC_EXISTING_USER_LINK_MODE` | `verified_email` |
| `OIDC_SCOPES` | `"openid email profile"` |
| `PLUGGY_CLIENT_ID` | `YourPluggyClientID` |
| `PLUGGY_CLIENT_SECRET` | `YourPluggyClientSecret` |

---

## 🎨 Step 3: Frontend Configuration (Web Service)

Create a new **Web Service** for the frontend connected to the same repository.

### 3.1 Build Settings (Context Directory)
Adjust the build paths to target the `/frontend` directory:

* **Dockerfile Path:** `./frontend/Dockerfile`
* **Docker Build Context Directory:** `./frontend`

### 3.2 Environment Variables (Preventing Error 508)

> ⚠️ **IMPORTANT:** On PaaS platforms (like Render), the edge proxy rewrites the `Host` header. To prevent the **`508 Loop Detected`** error in Nginx, the `PROXY_HOST` variable must contain the **pure backend domain** (without `https://` and trailing slashes).

Configure under the **Environment** tab:

| Key | Value Configured | Description |
| :--- | :--- | :--- |
| **`PROXY_HOST`** | **`securo-oic9.onrender.com`** | **Required:** Pure backend domain to prevent Nginx proxy looping. |
| `BACKEND_URL` | `https://securo-oic9.onrender.com` | Full public URL of the backend. |
| `VITE_API_URL` | `https://securo-oic9.onrender.com` | API endpoint URL used in the Vite bundle. |
| `NGINX_RESOLVER` | `8.8.8.8` | Primary DNS resolver for Nginx. |

---

## 🔍 Troubleshooting

* **`508 Loop Detected` Error on Frontend:**  
  Ensure that the `PROXY_HOST` environment variable in the frontend is set **without** the `https://` prefix (use only `your-backend-name.onrender.com`).
* **`invalid number of arguments in "proxy_set_header"` Error:**  
  Occurs when `PROXY_HOST` is left completely empty. A valid hostname is required to generate the Nginx configuration file properly.
* **`start.sh` Permission Denied Error:**  
  Verify that the `backend/start.sh` file has execution permissions set in the Git repository (`chmod +x backend/start.sh`).
