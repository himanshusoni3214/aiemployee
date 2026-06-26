import { LoginForm } from '../../components/LoginForm';

export default async function Login({ searchParams }: { searchParams?: Promise<Record<string, string | string[] | undefined>> }) {
  const params = searchParams ? await searchParams : {};
  const expired = params.expired;
  const notice = expired ? 'Session expired, please login again.' : '';
  return <LoginForm notice={notice} />;
}
