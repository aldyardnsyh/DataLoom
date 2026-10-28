"use client";

import { useQuery } from "@tanstack/react-query";

import { EmptyState } from "@/components/domain/empty-state";
import { ErrorState } from "@/components/domain/error-state";
import { BlockSkeleton } from "@/components/domain/state-loading";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { api, classifyError } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { t } from "@/lib/i18n/id";

/**
 * Audit log: append-only record of sensitive actions (who did what, from where).
 *
 * Read-only by design: there is no delete/edit control because the backend has
 * no such endpoint and offering one would be a lie. It sits in /admin because
 * the log spans the whole organization, not one person's activity.
 */
export function AuditPanel() {
  const auditQuery = useQuery({
    queryKey: ["audit-logs"],
    queryFn: () => api.settings.audit(),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t.settings.auditTitle}</CardTitle>
        <p className="mt-1 text-xs text-muted-foreground">{t.adminHub.auditBody}</p>
      </CardHeader>
      <CardContent className="p-0">
        {auditQuery.isLoading ? (
          <div className="p-4">
            <BlockSkeleton height={160} />
          </div>
        ) : auditQuery.isError ? (
          <div className="p-4">
            <ErrorState
              compact
              kind={classifyError(auditQuery.error).kind}
              message={classifyError(auditQuery.error).message}
              requestId={classifyError(auditQuery.error).requestId}
              onRetry={() => void auditQuery.refetch()}
            />
          </div>
        ) : (auditQuery.data ?? []).length === 0 ? (
          <EmptyState compact title={t.settings.emptyAudit} />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{t.settings.colTime}</TableHead>
                <TableHead>{t.settings.colAction}</TableHead>
                <TableHead>{t.settings.colActor}</TableHead>
                <TableHead>{t.settings.colTarget}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(auditQuery.data ?? []).map((entry) => (
                <TableRow key={entry.id}>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {formatDateTime(entry.at)}
                  </TableCell>
                  <TableCell className="font-mono text-xs">{entry.action}</TableCell>
                  <TableCell className="text-xs">{entry.actor_email ?? entry.actor_kind}</TableCell>
                  <TableCell className="text-xs">
                    {entry.target_type}
                    {entry.target_id ? (
                      <span className="ml-1 font-mono text-[11px] text-muted-foreground">
                        {entry.target_id.slice(0, 8)}
                      </span>
                    ) : null}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}