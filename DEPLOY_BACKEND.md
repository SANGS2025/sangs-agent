# Deploy Backend to Render.com

## Prerequisites

1. **PostgreSQL Database**: You'll need a PostgreSQL database. Render provides this.

## Step-by-Step Deployment

### 1. Create PostgreSQL Database on Render

1. Go to: https://render.com/dashboard
2. Click **"New +"** → **"PostgreSQL"**
3. Configure:
   - **Name**: `sangs-db`
   - **Database**: `sangs_agent` (or leave default)
   - **User**: (auto-generated)
   - **Region**: Choose closest to you
   - **PostgreSQL Version**: 15 or 16
4. Click **"Create Database"**
5. **Copy the Internal Database URL** (you'll need this)

### 2. Deploy Web Service

1. In Render dashboard, click **"New +"** → **"Web Service"**
2. **Connect GitHub** and select: `SANGS2025/sangs-agent`
3. Configure:
   - **Name**: `sangs-api`
   - **Environment**: `Python 3`
   - **Region**: Same as database
   - **Branch**: `main`
   - **Root Directory**: `.` (default)
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. **Environment Variables** (click "Add Environment Variable"):
   ```
   DATABASE_URL = <paste the Internal Database URL from step 1>
   JWT_SECRET = <generate with: openssl rand -hex 32>
   JWT_ISSUER = SANGS-JARVIS
   JWT_AUDIENCE = SANGS-STAFF
   JWT_ACCESS_TTL_SECONDS = 3600
   JWT_REFRESH_TTL_SECONDS = 1209600
   ```
5. Click **"Create Web Service"**

### 3. Generate JWT_SECRET

Run this command locally to generate a secure secret:
```bash
openssl rand -hex 32
```

Copy the output and paste it as the `JWT_SECRET` value.

### 4. Wait for Deployment

- Build will take 5-10 minutes
- You'll get a URL like: `https://sangs-api.onrender.com`
- Check logs for any errors

### 5. Initialize Database

After deployment, you need to run the database migrations:

**Option A: Using Render Shell**
1. Go to your web service in Render
2. Click "Shell" tab
3. Run:
   ```bash
   python3 run_migration.py
   python3 populate_public_tables.py
   ```

**Option B: Using Local Connection**
If you have the database connection string, you can run migrations locally:
```bash
cd /Users/dario/sangs-agent
export DATABASE_URL="<your-render-db-url>"
python3 run_migration.py
python3 populate_public_tables.py
```

### 6. Update Frontend Environment Variables

1. Go to: https://vercel.com/dashboard
2. Select `sangs-verify` project
3. Go to: **Settings** → **Environment Variables**
4. Update:
   - `API_BASE_URL` = `https://sangs-api.onrender.com` (your Render URL)
   - `NEXT_PUBLIC_API_BASE` = `https://sangs-api.onrender.com`
5. **Redeploy**: Go to Deployments → Click "..." → Redeploy

## Testing

After deployment:
1. Test health endpoint: `https://sangs-api.onrender.com/health`
2. Test login: `POST https://sangs-api.onrender.com/auth/login`
3. Test certificate lookup: `GET https://sangs-api.onrender.com/api/certs/2025-1275-002`

## Troubleshooting

- **Build fails**: Check that all dependencies are in `requirements.txt`
- **Database connection fails**: Verify `DATABASE_URL` is correct
- **CORS errors**: Make sure your Vercel URL is in `ALLOWED_ORIGINS` in `main.py`
- **502 errors**: Check Render logs for application errors

## Cost

- **Free tier**: 750 hours/month (enough for most use cases)
- **PostgreSQL**: Free tier available (limited connections)
- **Upgrade**: If you need more resources

