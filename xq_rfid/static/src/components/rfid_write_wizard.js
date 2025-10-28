/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState, onWillStart } from "@odoo/owl";

export class RfidWriteWizard extends Component {
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
            rfidTagName: '',
            rfidTagId: null,
            productionOrder: '',
            productName: '',
            productCode: '',
            batchNumber: '',
            productionDate: '',
            quantity: '',
            unit: '',
            workCenter: '',
            workorder: '',
            operation: '',
            operator: '',
        });

        onWillStart(async () => {
            await this.loadRfidTagData();
        });
    }

    async loadRfidTagData() {
        const recordData = this.props.record.data;
        
        console.log('RfidWriteWizard - recordData:', recordData);
        console.log('RfidWriteWizard - production_id:', recordData.production_id);
        
        try {
            // 首先检查当前记录是否已经有RFID标签
            if (recordData.rfid_tag_id && recordData.rfid_tag_id[0]) {
                this.state.rfidTagId = recordData.rfid_tag_id[0];
                this.state.rfidTagName = recordData.rfid_tag_name || '';
                
                // 获取RFID标签的详细信息
                await this.loadRfidTagDetails(recordData.rfid_tag_id[0]);
            } else {
                // 如果没有RFID标签，尝试从同一个生产订单的"RFID 标签"类型检查中获取
                await this.findRfidTagFromProduction();
            }
            
        } catch (error) {
            console.error('获取RFID标签数据失败:', error);
            // 如果出错，尝试从生产订单获取信息
            await this.loadProductionData();
        }
    }

    async findRfidTagFromProduction() {
        const recordData = this.props.record.data;
        
        try {
            // 获取当前生产订单
            if (recordData.production_id && recordData.production_id[0]) {
                // 查找同一个生产订单的"RFID 标签"类型的质量控制检查
                const rfidLabelChecks = await this.orm.searchRead(
                    'quality.check',
                    [
                        ['production_id', '=', recordData.production_id[0]],
                        ['point_id.test_type_id.technical_name', '=', 'rfid_label'],
                        ['rfid_tag_id', '!=', false]
                    ],
                    ['rfid_tag_id'],
                    { limit: 1 }
                );
                
                if (rfidLabelChecks && rfidLabelChecks.length > 0) {
                    const rfidTagId = rfidLabelChecks[0].rfid_tag_id[0];
                    this.state.rfidTagId = rfidTagId;
                    await this.loadRfidTagDetails(rfidTagId);
                } else {
                    // 如果没有找到RFID标签，从生产订单获取信息
                    await this.loadProductionData();
                }
            } else {
                await this.loadProductionData();
            }
            
        } catch (error) {
            console.error('查找RFID标签失败:', error);
            await this.loadProductionData();
        }
    }

    async loadRfidTagDetails(rfidTagId) {
        try {
            // 直接读取RFID标签的基本字段
            const rfidTag = await this.orm.searchRead(
                'rfid.tag',
                [['id', '=', rfidTagId]],
                ['name', 'production_id', 'stock_prod_lot_id', 'production_date']
            );
            
            if (rfidTag && rfidTag.length > 0) {
                const tag = rfidTag[0];
                
                // 设置基本字段
                this.state.rfidTagName = tag.name || '';
                
                // 处理生产日期 - 转换为本地时间
                if (tag.production_date) {
                    // Odoo返回的是UTC时间字符串，需要明确指定为UTC
                    const utcDateString = tag.production_date + ' UTC';
                    const utcDate = new Date(utcDateString);
                    
                    // 格式化为本地时间显示
                    const localDate = utcDate.toLocaleString('zh-CN', {
                        year: 'numeric',
                        month: '2-digit', 
                        day: '2-digit',
                        hour: '2-digit',
                        minute: '2-digit',
                        second: '2-digit',
                        hour12: false
                    });
                    
                    this.state.productionDate = localDate;
                } else {
                    this.state.productionDate = '';
                }
                
                // 获取生产订单信息
                if (tag.production_id && tag.production_id[0]) {
                    const production = await this.orm.read(
                        'mrp.production',
                        [tag.production_id[0]],
                        ['name']
                    );
                    if (production && production.length > 0) {
                        this.state.productionOrder = production[0].name || '';
                    }
                }
                
                // 获取批次信息
                if (tag.stock_prod_lot_id && tag.stock_prod_lot_id[0]) {
                    const lot = await this.orm.read(
                        'stock.lot',
                        [tag.stock_prod_lot_id[0]],
                        ['name']
                    );
                    if (lot && lot.length > 0) {
                        this.state.batchNumber = lot[0].name || '';
                    }
                }
                
            }
        } catch (error) {
            console.error('获取RFID标签详情失败:', error);
        }
    }

    async loadProductionData() {
        const recordData = this.props.record.data;
        
        try {
            // 获取生产订单信息
            if (recordData.production_id && recordData.production_id[0]) {
                const production = await this.orm.read(
                    'mrp.production',
                    [recordData.production_id[0]],
                    ['name', 'product_id', 'lot_producing_id', 'product_qty', 'product_uom_id']
                );
                
                if (production && production.length > 0) {
                    const prod = production[0];
                    this.state.productionOrder = prod.name || '';
                    this.state.batchNumber = prod.lot_producing_id ? prod.lot_producing_id[1] : '';
                    this.state.quantity = prod.product_qty || '';
                    
                    // 获取产品信息
                    if (prod.product_id && prod.product_id[0]) {
                        const product = await this.orm.read(
                            'product.product',
                            [prod.product_id[0]],
                            ['name', 'default_code']
                        );
                        
                        if (product && product.length > 0) {
                            this.state.productName = product[0].name || '';
                            this.state.productCode = product[0].default_code || '';
                        }
                    }
                    
                    // 获取单位信息
                    if (prod.product_uom_id && prod.product_uom_id[0]) {
                        const uom = await this.orm.read(
                            'uom.uom',
                            [prod.product_uom_id[0]],
                            ['name']
                        );
                        
                        if (uom && uom.length > 0) {
                            this.state.unit = uom[0].name || '';
                        }
                    }
                }
            }
            
            // 获取工单信息
            if (recordData.workorder_id && recordData.workorder_id[0]) {
                const workorder = await this.orm.read(
                    'mrp.workorder',
                    [recordData.workorder_id[0]],
                    ['name', 'workcenter_id']
                );
                
                if (workorder && workorder.length > 0) {
                    this.state.workorder = workorder[0].name || '';
                    
                    // 获取工作中心信息
                    if (workorder[0].workcenter_id && workorder[0].workcenter_id[0]) {
                        const workcenter = await this.orm.read(
                            'mrp.workcenter',
                            [workorder[0].workcenter_id[0]],
                            ['name']
                        );
                        
                        if (workcenter && workcenter.length > 0) {
                            this.state.workCenter = workcenter[0].name || '';
                        }
                    }
                }
            }
            
            // 获取质量控制点信息
            if (recordData.point_id && recordData.point_id[0]) {
                const point = await this.orm.read(
                    'quality.point',
                    [recordData.point_id[0]],
                    ['title']
                );
                
                if (point && point.length > 0) {
                    this.state.operation = point[0].title || '';
                }
            }
            
            // 设置当前时间
            this.state.productionDate = new Date().toLocaleString('zh-CN');
            
            // 获取当前用户信息
            const user = this.env.services.user;
            if (user && user.name) {
                this.state.operator = user.name;
            }
            
        } catch (error) {
            console.error('获取生产数据失败:', error);
        }
    }
}

RfidWriteWizard.template = "xq_rfid.RfidWriteWizard";

registry.category("components").add("RfidWriteWizard", RfidWriteWizard);
