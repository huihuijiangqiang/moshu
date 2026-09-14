# 生产部署

该目录只包含生产部署模板。真实配置文件、模型密钥和支付证书必须放在服务器上的仓库之外；它们不会被复制进镜像，也不应提交 Git。

## 首次配置

1. 将 `moshu.production.env.example` 复制到服务器受保护目录，例如 `/etc/moshu/moshu.production.env`，填写所有 `replace-*` 项。数据库密码、JWT 密钥、凭据加密密钥和一次性引导令牌都应使用密码生成器生成，至少 32 个字符。
2. 运行预检（不会打印任何密钥）：

   ```bash
   bash deploy/production/check-env.sh /etc/moshu/moshu.production.env
   ```

3. 使用外部 env 文件同时提供 Compose 插值和容器环境：

   ```bash
   export MOSHU_PRODUCTION_ENV_FILE=/etc/moshu/moshu.production.env
   docker compose --env-file "$MOSHU_PRODUCTION_ENV_FILE" -f docker-compose.production.yml config --quiet
   docker compose --env-file "$MOSHU_PRODUCTION_ENV_FILE" -f docker-compose.production.yml up -d --build
   ```

生产 Compose 只发布 `127.0.0.1:5180` 的前端端口。API、PostgreSQL 和 Redis 没有宿主端口映射，只能通过内部网络访问；Cloudflare Tunnel 应指向服务器本机的 `http://127.0.0.1:5180`。

## 首个管理员

`BOOTSTRAP_TOKEN` 仅用于空库首次调用 `POST /auth/bootstrap`。创建 `super_admin` 后，立即从服务器 env 文件删除该变量并重启 API；之后使用正常登录和管理员界面，不要长期开放 bootstrap 令牌。

## 运维检查

```bash
docker compose --env-file "$MOSHU_PRODUCTION_ENV_FILE" -f docker-compose.production.yml ps
docker compose --env-file "$MOSHU_PRODUCTION_ENV_FILE" -f docker-compose.production.yml logs --tail=100 api worker
curl --fail http://127.0.0.1:5180/
```

服务设有健康检查、非 root 运行、禁止新增 Linux 权限、只读应用文件系统、单 worker 并发和 JSON 日志轮转。PostgreSQL/Redis 使用命名卷；应按服务器策略定期备份卷和外部 S3 存储。`docker compose down` 不会删除卷，清理数据必须显式执行并先确认备份。

## 更新与回滚

构建新版本前先记录当前镜像摘要或 Git revision。更新时执行 `up -d --build`，确认 `api` 与 `frontend` 健康后再清理旧镜像；失败时回到上一个 revision 重新构建并启动。迁移服务在 API 启动前执行 `alembic upgrade head`，迁移失败会阻止 API 启动。
