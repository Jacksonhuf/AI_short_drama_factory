// Docker/Nginx 可在部署时注入 BACKEND_URL；本地开发会回退到源码中的 localhost 默认值。
window.__ENV = window.__ENV || {}
