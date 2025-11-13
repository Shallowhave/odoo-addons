/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";

export class ComponentScanWizard extends Component {
    static props = {
        name: { type: String, optional: true },
        record: Object,
        close: Function,
        validate: Function,
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        
        this.state = useState({
            scannedBarcode: '',
            scannedProduct: null,
            scannedProductName: '',
            scannedProductCode: '',
            requiredComponents: [],
            verificationResult: 'pending',
            verificationMessage: '',
            isScanning: false,
        });

        onWillStart(async () => {
            await this.loadRequiredComponents();
        });
    }

    async loadRequiredComponents() {
        const recordData = this.props.record.data;
        
        try {
            // 获取生产订单的组件列表
            if (recordData.production_id && recordData.production_id[0]) {
                const production = await this.orm.read(
                    'mrp.production',
                    [recordData.production_id[0]],
                    ['name', 'move_raw_ids']
                );
                
                if (production && production.length > 0) {
                    const moveIds = production[0].move_raw_ids || [];
                    
                    if (moveIds.length > 0) {
                        const moves = await this.orm.read(
                            'stock.move',
                            moveIds,
                            ['product_id', 'product_uom_qty']
                        );
                        
                        const components = [];
                        for (const move of moves) {
                            if (move.product_id && move.product_id[0]) {
                                const product = await this.orm.read(
                                    'product.product',
                                    [move.product_id[0]],
                                    ['name', 'default_code']
                                );
                                
                                if (product && product.length > 0) {
                                    components.push({
                                        id: product[0].id,
                                        name: product[0].name,
                                        code: product[0].default_code || '',
                                        quantity: move.product_uom_qty || 0,
                                    });
                                }
                            }
                        }
                        
                        this.state.requiredComponents = components;
                    }
                }
            }
        } catch (error) {
            console.error('获取组件列表失败:', error);
            this.notification.add('获取组件列表失败', { type: 'danger' });
        }
    }

    async onBarcodeScanned(ev) {
        // 支持pad设备扫码：监听输入事件，自动识别扫码输入
        const barcode = ev.target.value.trim();
        
        if (!barcode) {
            return;
        }
        
        // 延迟处理，避免输入过程中的中间值触发
        clearTimeout(this._scanTimeout);
        this._scanTimeout = setTimeout(async () => {
            await this.processBarcode(barcode);
            ev.target.value = ''; // 清空输入框，准备下次扫码
        }, 300); // 300ms延迟，确保扫码完成
    }
    
    async processBarcode(barcode) {
        if (!barcode) {
            return;
        }
        
        this.state.isScanning = true;
        this.state.scannedBarcode = barcode;
        
        try {
            // 通过条码查找产品
            const products = await this.orm.searchRead(
                'product.product',
                [
                    '|',
                    ['barcode', '=', barcode],
                    ['default_code', '=', barcode]
                ],
                ['name', 'default_code', 'barcode'],
                { limit: 1 }
            );
            
            if (products && products.length > 0) {
                const product = products[0];
                this.state.scannedProduct = product.id;
                this.state.scannedProductName = product.name;
                this.state.scannedProductCode = product.default_code || product.barcode || '';
                
                // 验证组件是否匹配
                await this.verifyComponent(product.id);
            } else {
                // 尝试通过批次号查找
                const lots = await this.orm.searchRead(
                    'stock.lot',
                    [['name', '=', barcode]],
                    ['product_id'],
                    { limit: 1 }
                );
                
                if (lots && lots.length > 0 && lots[0].product_id) {
                    const productId = lots[0].product_id[0];
                    const product = await this.orm.read(
                        'product.product',
                        [productId],
                        ['name', 'default_code', 'barcode']
                    );
                    
                    if (product && product.length > 0) {
                        this.state.scannedProduct = product[0].id;
                        this.state.scannedProductName = product[0].name;
                        this.state.scannedProductCode = product[0].default_code || product[0].barcode || '';
                        
                        // 验证组件是否匹配
                        await this.verifyComponent(product[0].id);
                    } else {
                        this.state.verificationResult = 'mismatched';
                        this.state.verificationMessage = '未找到对应的产品';
                    }
                } else {
                    this.state.verificationResult = 'mismatched';
                    this.state.verificationMessage = '未找到对应的产品或批次';
                }
            }
        } catch (error) {
            console.error('扫码验证失败:', error);
            this.notification.add('扫码验证失败: ' + String(error), { type: 'danger' });
            this.state.verificationResult = 'mismatched';
            this.state.verificationMessage = '验证失败: ' + String(error);
        } finally {
            this.state.isScanning = false;
        }
    }
    
    onKeyDown(ev) {
        // 支持Enter键快速确认（pad设备扫码后通常会自动回车）
        if (ev.key === 'Enter' && this.state.scannedBarcode) {
            ev.preventDefault();
            // Enter键触发扫码处理
            if (this._scanTimeout) {
                clearTimeout(this._scanTimeout);
            }
            this.processBarcode(this.state.scannedBarcode);
        }
    }

    async verifyComponent(productId) {
        const recordData = this.props.record.data;
        
        try {
            // 检查组件是否在需要的组件列表中
            const isMatched = this.state.requiredComponents.some(
                comp => comp.id === productId
            );
            
            if (isMatched) {
                this.state.verificationResult = 'matched';
                this.state.verificationMessage = '组件验证成功！';
                
                // 保存到质检记录
                await this.orm.write('quality.check', [recordData.id], {
                    scanned_component_id: productId,
                    scanned_component_code: this.state.scannedProductCode,
                    component_verification_result: 'matched',
                    component_verification_message: this.state.verificationMessage,
                });
                
                this.notification.add('组件验证成功', { type: 'success' });
            } else {
                this.state.verificationResult = 'mismatched';
                const requiredNames = this.state.requiredComponents.map(c => c.name).join(', ');
                this.state.verificationMessage = `组件不匹配！需要的组件：${requiredNames}`;
                
                // 保存到质检记录
                await this.orm.write('quality.check', [recordData.id], {
                    scanned_component_id: productId,
                    scanned_component_code: this.state.scannedProductCode,
                    component_verification_result: 'mismatched',
                    component_verification_message: this.state.verificationMessage,
                });
                
                this.notification.add(this.state.verificationMessage, { type: 'danger' });
            }
        } catch (error) {
            console.error('验证组件失败:', error);
            this.notification.add('验证组件失败: ' + String(error), { type: 'danger' });
        }
    }

    async onValidate() {
        if (this.state.verificationResult === 'matched') {
            // 调用父类的 validate 方法继续流程
            if (this.props.validate) {
                return this.props.validate();
            }
        } else if (this.state.verificationResult === 'mismatched') {
            this.notification.add('组件不匹配，无法通过验证', { type: 'danger' });
        } else {
            this.notification.add('请先扫码确认组件', { type: 'warning' });
        }
    }
}

ComponentScanWizard.template = "mrp_component_scan_verify.ComponentScanWizard";

registry.category("components").add("ComponentScanWizard", ComponentScanWizard);

