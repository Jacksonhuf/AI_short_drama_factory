import { LockOutlined } from '@ant-design/icons'
import { Alert, Button, Card, Form, Input, Typography } from 'antd'
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'
import { ApiError, AuthService } from '../services/generated'
import { useAuthStore } from '../store/useAuthStore'

type LoginValues = { password: string }
type SessionResponse = {
  data?: { authenticated?: boolean; username?: string | null }
}

/**
 * Presents the administrator password gate without persisting the password or session token.
 */
export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const { authenticated, setSession } = useAuthStore()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const from = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname ?? '/projects'

  /** Submits the password to the generated client and records only the returned session state. */
  const handleSubmit = async ({ password }: LoginValues) => {
    setSubmitting(true)
    setError(null)
    try {
      const response = (await AuthService.loginApiV1AuthLoginPost({
        requestBody: { password },
      })) as SessionResponse
      if (!response.data?.authenticated) {
        throw new Error('登录状态未建立，请重试')
      }
      setSession(true, response.data.username)
      navigate(from, { replace: true })
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setError('管理员密码不正确，请重试。')
      } else {
        setError('暂时无法登录，请检查服务状态后重试。')
      }
    } finally {
      setSubmitting(false)
    }
  }

  if (authenticated) {
    return <Navigate to="/projects" replace />
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-violet-50 px-4 py-8">
      <Card className="w-full max-w-md shadow-sm" bordered>
        <div className="mb-8 text-center">
          <div className="mb-3 text-3xl font-semibold text-indigo-600">元风</div>
          <Typography.Title level={3} className="!mb-2">
            管理员登录
          </Typography.Title>
          <Typography.Paragraph type="secondary" className="!mb-0">
            输入部署环境配置的管理员密码以继续。
          </Typography.Paragraph>
        </div>

        {error && (
          <Alert className="mb-4" type="error" message={error} role="alert" showIcon />
        )}

        <Form<LoginValues> layout="vertical" requiredMark={false} onFinish={handleSubmit}>
          <Form.Item
            label="管理员密码"
            name="password"
            rules={[{ required: true, message: '请输入管理员密码。' }]}
          >
            <Input.Password
              autoComplete="current-password"
              prefix={<LockOutlined aria-hidden="true" />}
              placeholder="请输入密码"
              size="large"
            />
          </Form.Item>
          <Button className="w-full" type="primary" htmlType="submit" size="large" loading={submitting}>
            登录
          </Button>
        </Form>
      </Card>
    </main>
  )
}
