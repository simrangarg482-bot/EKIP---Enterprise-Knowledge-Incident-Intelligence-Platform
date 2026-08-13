import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Organization, Project } from "@/types/tenancy";
import { listOrganizations, listProjects } from "@/api/tenancy";
import { useAuth } from "./AuthContext";

interface TenantContextValue {
  organization: Organization | null;
  organizations: Organization[];
  project: Project | null;
  projects: Project[];
  isLoading: boolean;
  setOrganization: (org: Organization) => void;
  setProject: (project: Project) => void;
}

const TenantContext = createContext<TenantContextValue | undefined>(undefined);

export function TenantProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated, isLoading: isAuthLoading } = useAuth();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [organization, setOrganization] = useState<Organization | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Wait for AuthProvider to resolve (and set the access token) first --
    // fetching tenancy data before authentication settles sends requests
    // with no Authorization header at all, since it's a sibling/parent
    // provider whose own effect hasn't necessarily run yet.
    if (isAuthLoading) return;
    if (!isAuthenticated) {
      setIsLoading(false);
      return;
    }
    listOrganizations()
      .then((orgs) => {
        setOrganizations(orgs);
        if (orgs.length > 0) setOrganization(orgs[0]);
      })
      .finally(() => setIsLoading(false));
  }, [isAuthenticated, isAuthLoading]);

  useEffect(() => {
    if (!organization) {
      setProjects([]);
      setProject(null);
      return;
    }
    listProjects(organization.id).then((orgProjects) => {
      setProjects(orgProjects);
      setProject(orgProjects[0] ?? null);
    });
  }, [organization]);

  const value = useMemo<TenantContextValue>(
    () => ({
      organization,
      organizations,
      project,
      projects,
      isLoading,
      setOrganization,
      setProject,
    }),
    [organization, organizations, project, projects, isLoading],
  );

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenant(): TenantContextValue {
  const ctx = useContext(TenantContext);
  if (!ctx) throw new Error("useTenant must be used within a TenantProvider");
  return ctx;
}
