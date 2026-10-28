"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/domain/confirm-dialog";
import { ErrorState } from "@/components/domain/error-state";
import { StatusBadge } from "@/components/domain/status-badge";
import { BlockSkeleton, LoadingAnnouncer } from "@/components/domain/state-loading";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCanMutate } from "@/components/providers/session-provider";
import { api, classifyError } from "@/lib/api";
import { ROLE_OPTIONS } from "@/lib/status";
import { t } from "@/lib/i18n/id";
import type { Membership } from "@/lib/types";

/**
 * Team administration: who belongs to the organization and with which role.
 *
 * Lives under /admin because it is policy, not personal preference: changing a
 * role grants or removes the ability to mutate data for everyone else, and
 * removing a member revokes their sessions immediately.
 *
 * Owner is immutable from this screen on purpose: demoting the last owner would
 * leave the organization with nobody able to administer it.
 */
export function TeamPanel() {
  const canMutate = useCanMutate();
  const queryClient = useQueryClient();
  const [removingMember, setRemovingMember] = useState<Membership | null>(null);

  const membersQuery = useQuery({
    queryKey: ["members"],
    queryFn: () => api.settings.members(),
  });

  const roleMutation = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) => api.settings.updateRole(id, role),
    onSuccess: async () => {
      toast.success(t.settings.saved);
      await queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  const removeMutation = useMutation({
    mutationFn: (membershipId: string) => api.settings.removeMember(membershipId),
    onSuccess: async () => {
      toast.success(t.settings.memberRemoved);
      setRemovingMember(null);
      await queryClient.invalidateQueries({ queryKey: ["members"] });
    },
    onError: (error: Error) => toast.error(error.message),
  });

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <div className="min-w-0">
            <CardTitle>{t.settings.tabTeam}</CardTitle>
            <p className="mt-1 text-xs text-muted-foreground">{t.adminHub.teamBody}</p>
          </div>
          <Button size="sm" variant="outline" disabled title={t.adminHub.inviteUnavailable}>
            {t.settings.invite}
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          {membersQuery.isLoading ? (
            <div className="p-4">
              <LoadingAnnouncer />
              <BlockSkeleton height={120} />
            </div>
          ) : membersQuery.isError ? (
            <div className="p-4">
              <ErrorState
                compact
                kind={classifyError(membersQuery.error).kind}
                message={classifyError(membersQuery.error).message}
                requestId={classifyError(membersQuery.error).requestId}
                onRetry={() => void membersQuery.refetch()}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.settings.colMember}</TableHead>
                  <TableHead>{t.settings.colRole}</TableHead>
                  <TableHead>{t.settings.colStatusMember}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {(membersQuery.data ?? []).map((member) => {
                  const initials = member.user_name
                    .split(" ")
                    .map((part) => part[0])
                    .slice(0, 2)
                    .join("")
                    .toUpperCase();
                  return (
                    <TableRow key={member.id}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <Avatar>
                            <AvatarFallback>{initials}</AvatarFallback>
                          </Avatar>
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium">{member.user_name}</p>
                            <p className="truncate text-xs text-muted-foreground">{member.user_email}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Select
                          value={member.role}
                          onValueChange={(value) => roleMutation.mutate({ id: member.id, role: value })}
                          disabled={!canMutate || member.role === "owner"}
                        >
                          <SelectTrigger className="w-32" aria-label={`Peran ${member.user_name}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {ROLE_OPTIONS.map((option) => (
                              <SelectItem key={option.value} value={option.value}>
                                {option.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        <StatusBadge status="active" showIcon />
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive-text hover:text-destructive-text"
                          disabled={!canMutate || member.role === "owner"}
                          title={
                            member.role === "owner"
                              ? t.adminHub.ownerImmutable
                              : canMutate
                                ? undefined
                                : t.state.disabledTooltip
                          }
                          onClick={() => setRemovingMember(member)}
                        >
                          {t.settings.remove}
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={removingMember !== null}
        onOpenChange={(open) => {
          if (!open) setRemovingMember(null);
        }}
        title={t.settings.removeMemberTitle}
        description={t.settings.removeMemberWarning}
        resourceName={removingMember?.user_name ?? ""}
        confirmLabel={t.settings.remove}
        onConfirm={() => {
          if (removingMember) removeMutation.mutate(removingMember.id);
        }}
        pending={removeMutation.isPending}
      />
    </>
  );
}