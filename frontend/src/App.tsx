import { createBrowserRouter, Navigate, RouterProvider, useNavigate } from 'react-router-dom';
import { AccountPage } from './AccountPage';
import AdminPage from './AdminPage';
import { AppearancePage } from './AppearancePage';
import CustomerLayout from './CustomerLayout';
import { useCustomer } from './customerState';
import HistoryPage from './HistoryPage';
import MerchantClickDetailPage from './MerchantClickDetail';
import MerchantGate from './MerchantGate';
import { OutfitsPage } from './OutfitsPage';
import { TryOnPage } from './TryOnPage';
import WorkspacePage from './WorkspacePage';

/**
 * Every customer, merchant and operator address is a real URL: the workspace
 * is `/`, the wardrobe pages have their own paths, the shop console is
 * `/merchant` and the operator console is `/admin`.
 */

function TryOnRoute() {
  const { caps, ready, addJob, chooseJob } = useCustomer();
  const navigate = useNavigate();
  return <TryOnPage caps={caps} ready={ready} onContinue={(created) => {
    addJob(created); chooseJob(created); navigate('/');
  }} />;
}

function OutfitsRoute() {
  const { ready } = useCustomer();
  const navigate = useNavigate();
  return <OutfitsPage ready={ready} onModeling={() => navigate('/')} />;
}

function AccountRoute() {
  const { account, theme, setTheme } = useCustomer();
  return <AccountPage key={account.user?.id || 'anonymous'} user={account.user} onChanged={account.refresh}>
    <AppearancePage theme={theme} onTheme={setTheme} />
  </AccountPage>;
}

const router = createBrowserRouter([
  // The operator console is its own app shell: an admin is not a customer.
  { path: '/admin', element: <AdminPage /> },
  {
    path: '/',
    element: <CustomerLayout />,
    children: [
      { index: true, element: <WorkspacePage /> },
      { path: 'tryon', element: <TryOnRoute /> },
      { path: 'outfits', element: <OutfitsRoute /> },
      { path: 'merchant', element: <MerchantGate /> },
      // The click ranking behind the console's data cards; an address without a
      // period falls back to the lifetime total.
      { path: 'merchant/analytics', element: <Navigate to="/merchant/analytics/total" replace /> },
      { path: 'merchant/analytics/:range', element: <MerchantClickDetailPage /> },
      { path: 'history', element: <HistoryPage /> },
      { path: 'account', element: <AccountRoute /> },
      { path: 'appearance', element: <Navigate to="/account" replace /> },
      // Any other address belongs to no page yet; send it to the workbench.
      { path: '*', element: <Navigate to="/" replace /> },
    ],
  },
]);

export default function App() {
  return <RouterProvider router={router} />;
}
