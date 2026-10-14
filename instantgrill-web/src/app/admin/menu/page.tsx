import { AdminPage } from "@/components/admin/gate";
import { MenuEditor } from "@/components/admin/MenuEditor";
import { products as catalog } from "@/lib/catalog";
import { getFullMenu } from "@/lib/menu";

export default function AdminMenuPage() {
  return (
    <AdminPage title="Menu & areas">
      {async () => {
        const menu = await getFullMenu();
        const rows = menu.products.map((p) => ({ ...p, basePrice: catalog.find((c) => c.slug === p.slug)!.price }));
        return <MenuEditor products={rows} areas={menu.areas} />;
      }}
    </AdminPage>
  );
}
