import { Spin } from 'antd'
import { useEffect } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { AuthService } from '../services/generated'
import { useAuthStore } from '../store/useAuthStore'

type SessionResponse = {
  data?: { authenticated?: boolean; username?: string | null }
}

/**
 * Restores the HttpOnly session before rendering protected routes.
 * This keeps browser storage free of passwords and authentication tokens.
 */
export default function RequireAuth() {
  const location = useLocation()
  const { authenticated, sessionChecked, setSession } = useAuthStore()

  useEffect(() => {
    let active = true

    /** Requests the server-authoritative authentication state once per app load. */
    const restoreSession = async () => {
      try {
        const response = (await AuthService.sessionApiV1AuthSessionGet()) as SessionResponse
        if (active) {
          setSession(Boolean(response.data?.authenticated), response.data?.username)
        }
      } catch {
        if (active) {
          setSession(false)
        }
      }
    }

    if (!sessionChecked) {
      void restoreSession()
    }

    return () => {
      active = false
    }
  }, [sessionChecked, setSession])

  if (!sessionChecked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50">
        <Spin size="large" tip="正在验证登录状态" />
      </div>
    )
  }

  if (!authenticated) {
    return <Navigate to="/login" replace state={{ from: location }} />
  }

  return <Outlet />
}
