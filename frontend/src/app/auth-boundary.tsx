import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useCurrentUserApiV1AuthMeGet } from "@/api/generated/client";
import { authStore } from "@/api/http";
import { ErrorState, LoadingState } from "@/components/page";

export function AuthBoundary() {
  const location = useLocation();
  const queryClient = useQueryClient();
  const [token, setToken] = useState(authStore.get());
  const user = useCurrentUserApiV1AuthMeGet({ query: { enabled: Boolean(token), retry: false } });

  useEffect(() => {
    const clear = () => {
      setToken(null);
      // 不清缓存的话，换账号登录后会先渲染上一个用户的身份和数据。
      queryClient.clear();
    };
    window.addEventListener("tupai:unauthorized", clear);
    return () => window.removeEventListener("tupai:unauthorized", clear);
  }, [queryClient]);

  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  if (user.isPending) return <LoadingState />;
  if (user.isError) return <ErrorState retry={() => void user.refetch()} />;
  return <Outlet context={{ user: user.data }} />;
}
