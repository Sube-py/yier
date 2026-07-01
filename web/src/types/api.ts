export interface AuthSessionResponse {
  enabled: boolean
  authenticated: boolean
}

export interface AuthLoginRequest {
  password: string
}
